"""Portfolio builder — async run sub-algos + weight-blend into a portfolio.

This module orchestrates the MIXED mode of :class:`AlgoSignalCollector`:

  1. :meth:`PortfolioRunner.run_sub_algos` (Phase 1): async-gather each
     sub-algo's full backtest. Each sub-algo runs INDEPENDENTLY via the shared
     runner, writing its own ``strategy_identity`` row (``strategy_name =
     algo_name``) with skip-if-already-found (idempotent). Sub-algos with
     weight 0 are skipped. A connection POOL is used so the sub-algos run
     concurrently — a single asyncpg connection serializes queries.

  2. :meth:`PortfolioRunner.build_algo_portfolio` (Phase 2): after sub-algos
     finish, run the PORTFOLIO backtest using the collector's blended
     fetch+apply+engine. The portfolio is a NEW ``strategy_identity``
     (``strategy_name = portfolio_name(selection)``) that combines sub-algo
     signals by weight into one consolidated ``signal_confidence``, then runs
     the same signal-agnostic engine. The portfolio's OHLC period
     (start_date / end_date on strategy_identity) is the union of sub-algo
     periods.

Position-awareness
------------------
The blend is only valid for NON-POSITION-AWARE algos
(``POSITION_AWARE = False``): position-aware algos' signals depend on their
own portfolio state, so blending their signals across algos would be
inconsistent. All current algos (macd) are position-irrelevant — their
signals depend only on market data. The check is enforced in
:func:`check_position_aware` before Phase 1 starts.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import pandas as pd

from strategy.factors_and_algos import get_algo
from strategy.factors_and_algos._algo.registry import short_name
from strategy.factors_and_algos._algo.fault_tolerance import append_ft_suffix

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Portfolio naming
# ---------------------------------------------------------------------------

#: Trained keys the Phase-2 portfolio run applies from its algo_configs
#: row (written by the mixed-mode trainer: Set C comb + Set A threshold +
#: Set B execution keys). Applied over the trading layer so tuned values
#: beat the STRATEGY_PARAMS defaults — the mixed mirror of the binary
#: path's load_params precedence.
_TRAINED_PORTFOLIO_KEYS = (
    "conf_threshold",
    "buy_exec_delay",
    "sell_exec_delay",
    "min_holding_period",
)


def portfolio_name(
    selection: Dict[str, float],
    fault_tolerance: float = 0,
) -> str:
    """Build the portfolio strategy_name from the family selection.

    e.g. {"macd": 1.0, "mov_rsi": 1.0} -> "portfolio:macd+mov_rsi"
    With fault_tolerance=10 -> "portfolio:macd+mov_rsi_ft10"

    Weighting was REMOVED (2026-11-07, user-directed "remove weighting
    from signals"): a mixed selection blends at EQUAL weight and the
    name carries ONLY the family names (a legacy weight value in the
    dict is ignored — the CLI no longer produces one). The name is
    stable (sorted by family name) so the same selection always maps to
    the same strategy_name — this is what makes the portfolio idempotent
    (skip-if-already-found in strategy_identity).
    """
    parts = [short_name(name) for name in sorted(selection)]
    base = "portfolio:" + ("+".join(parts) if parts else "empty")
    return append_ft_suffix(base, fault_tolerance)


# ---------------------------------------------------------------------------
# Signal blending
# ---------------------------------------------------------------------------
def blend_signal_confidence(
    per_algo_conf: Dict[str, float],
    weights: Dict[str, float],
) -> float:
    """Weight-blend per-algo signal_confidence into one signed value.

    ``per_algo_conf``: ``{algo_name: signal_confidence}`` for one (code, date).
    ``weights``: ``{algo_name: weight}``.

    Returns the blended signal in [-100, 100]. BUY takes priority when both
    sides fire on the same bar (net positive -> BUY; net negative -> SELL),
    mirroring the binary consolidate rule. Missing algos (not in
    ``per_algo_conf`` or NaN) contribute 0.
    """
    blended = 0.0
    for name, weight in weights.items():
        if weight == 0:
            continue
        conf = per_algo_conf.get(name)
        if conf is None or (isinstance(conf, float) and conf != conf):
            continue  # NaN guard
        blended += weight * float(conf)
    # Clip to [-100, 100].
    if blended > 100.0:
        return 100.0
    if blended < -100.0:
        return -100.0
    return blended


def check_position_aware(selection: Dict[str, float]) -> None:
    """Verify all selected algos are position-irrelevant (POSITION_AWARE=False).

    Raises ``ValueError`` if any algo is position-aware — the blend is only
    valid when each algo's signal on a given (code, date) is independent of
    the others' trading decisions.
    """
    for name, weight in selection.items():
        if weight == 0:
            continue
        algo = get_algo(name)
        if getattr(algo, "POSITION_AWARE", False):
            raise ValueError(
                f"algo '{name}' is POSITION_AWARE=True; the portfolio blend "
                f"is only valid for position-irrelevant algos. Exclude "
                f"'{name}' or set POSITION_AWARE=False in its config."
            )


# ---------------------------------------------------------------------------
# Phase 1 + 2 orchestrator (conn/pool-scoped)
# ---------------------------------------------------------------------------
class PortfolioRunner:
    """Conn/pool-scoped orchestrator for the mixed-mode run (Phase 1
    sub-algos on pooled connections + Phase 2 blended portfolio).

    Code motion of the former module-level conn/pool-first functions: each
    keeps its name, sequencing, and log lines. ``__init__`` absorbs the
    state that is identical at BOTH call sites in
    ``strategy.singleton_trading.__main__`` (mixed branch): conn (Phase 2),
    pool (Phase 1), collector (Phase 2, mixed-mode), selection,
    trading_layer, fault_tolerance, force, seq_no, t0. ``dry_run`` stays a
    method parameter because the two call sites pass different expressions
    (Phase 1 is invoked with dry_run=False, Phase 2 with args.dry_run);
    sec_type / codes vary per sec_type-loop iteration and stay method
    parameters. Pure helpers (portfolio_name, blend_signal_confidence,
    check_position_aware) remain module-level above.
    """

    def __init__(
        self,
        conn,
        pool,
        collector,
        selection: Dict[str, float],
        *,
        trading_layer: dict,
        fault_tolerance: float = 0,
        comb_overrides: Optional[dict] = None,
        force: bool,
        seq_no: Optional[int],
        t0: float,
    ) -> None:
        self.conn = conn              # Phase 2 (build_algo_portfolio)
        self.pool = pool              # Phase 1 (run_sub_algos)
        self.collector = collector    # Phase 2 (mixed-mode collector)
        self.selection = selection
        self.trading_layer = trading_layer
        self.fault_tolerance = fault_tolerance
        #: CLI-sourced comb overrides (--comb-json); merged over the
        #: portfolio's algo_configs row (config sourcing ONLY — the
        #: precedence itself lives in _comb.config.resolve_comb).
        self.comb_overrides = comb_overrides
        self.force = force
        self.seq_no = seq_no
        self.t0 = t0

    async def _run_one_sub_algo(
        self,
        *,
        algo_name: str,
        sec_type: str,
        codes: List[str],
        dry_run: bool,
    ) -> str:
        """Run ONE sub-algo's full backtest on its own pooled connection.

        Loads the algo's DB-backed params (algo defaults < DB < trading-layer
        overrides), then calls the shared ``run_one_sec_type``. The strategy_name
        stored in strategy_identity is the algo_name itself (e.g.
        "macd"), or ``algo_name_ft{N}`` when fault_tolerance > 0,
        so each algo's runs are stored and queried under their own name.
        skip-if-already-found is handled inside ``upsert_strategy_seq``.

        Returns the algo_name (for logging).
        """
        from strategy._common.runner import StrategyRunner
        from strategy.factors_and_algos import (
            AlgoSignalCollector, ensure_default_config, load_params,
        )

        strategy_name = append_ft_suffix(algo_name, self.fault_tolerance)

        async with self.pool.acquire() as conn:
            # Ensure a default algo_configs row exists for each code (so
            # load_params finds something). Idempotent.
            for code in codes:
                inserted = await ensure_default_config(
                    conn, algo_name, sec_type, code, algo_name,
                )
                if inserted:
                    logger.info(f"    [algo_configs] inserted default {algo_name} "
                          f"config for {sec_type}/{code}")
            # Load merged params (algo DEFAULT < DB < trading-layer overrides).
            # Uses the FIRST code as the config source (params are per-algo, not
            # per-code — they share the same algo_configs strategy_name).
            primary_code = codes[0] if codes else ""
            params = await load_params(
                conn, algo_name, sec_type, primary_code, algo_name,
                strategy_overrides=self.trading_layer,
            )

            # Binary collector for this one algo.
            collector = AlgoSignalCollector({algo_name: 1.0})
            await StrategyRunner(
                conn, force=self.force, seq_no=self.seq_no,
                dry_run=dry_run, t0=self.t0,
            ).run_one_sec_type(
                strategy_name=strategy_name,
                sec_type=sec_type,
                codes=codes,
                params=params,
                fetch_signal_fn=collector.fetch_signal_data,
                backtest_fn=collector.run_backtest,
                daily_fn=collector.compute_daily_rows,
            )
        return algo_name

    async def run_sub_algos(
        self,
        *,
        sec_type: str,
        codes: List[str],
        dry_run: bool,
    ) -> List[str]:
        """Phase 1: async-gather each sub-algo's full backtest.

        Each sub-algo (weight != 0) runs independently on its own pooled
        connection via :func:`_run_one_sub_algo`. ``asyncio.gather`` runs them
        concurrently. Returns the list of algo_names that ran.

        ``trading_layer`` carries the engine-consumed keys (min_holding_period,
        buy_notional, skip_final_liquidation) shared across all sub-algos.
        ``fault_tolerance`` > 0 appends ``_ft{N}`` to each sub-algo's
        strategy_name (so FT and non-FT runs coexist in the DB).
        """
        check_position_aware(self.selection)

        tasks = []
        ran_names: List[str] = []
        for algo_name, weight in self.selection.items():
            if weight == 0:
                continue
            ran_names.append(algo_name)
            tasks.append(
                self._run_one_sub_algo(
                    algo_name=algo_name,
                    sec_type=sec_type,
                    codes=codes,
                    dry_run=dry_run,
                )
            )
        if not tasks:
            logger.info("  [portfolio] no sub-algos with non-zero weight; skipping Phase 1.")
            return []

        logger.info(f"\n[Phase 1] Running {len(tasks)} sub-algo(s) concurrently: "
              f"{ran_names}")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for name, res in zip(ran_names, results):
            if isinstance(res, Exception):
                logger.error(f"    [Phase 1] {name}: ERROR {type(res).__name__}: {res}")
            else:
                logger.info(f"    [Phase 1] {name}: done")
        return ran_names

    async def build_algo_portfolio(
        self,
        *,
        sec_type: str,
        codes: List[str],
        dry_run: bool,
    ) -> None:
        """Phase 2: run the portfolio backtest (blended signals).

        The portfolio uses the collector's MIXED-mode fetch (union of per-algo
        data) + apply (weight-blend) + the same signal-agnostic engine. The
        strategy_name stored in strategy_identity is
        ``portfolio_name(selection, fault_tolerance)`` — a stable name derived
        from the algo->weight map (plus ``_ft{N}`` suffix when FT > 0) so the
        portfolio is idempotent (skip-if-already-found).

        After the backtest, risks are computed for the portfolio
        (same as a standalone algo run) — handled by the caller (__main__.py)
        to keep this module focused on the backtest itself.
        """
        from strategy._common.runner import StrategyRunner
        from strategy.factors_and_algos import load_algo_config, load_config_source
        from strategy.factors_and_algos._comb import PARAMS_VERSION, resolve_comb

        pf_name = portfolio_name(self.selection, fault_tolerance=self.fault_tolerance)
        logger.info(f"\n[Phase 2] Building portfolio '{pf_name}' "
              f"(blended backtest over {len(codes)} code(s))...")

        # The collector is in MIXED mode (multiple algos). Its fetch/apply/
        # backtest handle the blend internally. The params dict carries the
        # trading-layer keys; per-algo keys are loaded by the collector's
        # _fetch_mixed (each algo's apply_signals uses its own DEFAULT_PARAMS
        # merged with any DB config — threaded via the collector's stored algos).
        #
        # For simplicity, the portfolio run uses the trading_layer dict as its
        # params (the collector's _blend_signals loads each algo's params from
        # the algo modules directly, not from this dict). The engine reads
        # min_holding_period / buy_notional / skip_final_liquidation from here.
        params = dict(self.trading_layer)

        # Comb config (2026-11-17 measured stack, default ON): precedence
        # code-defaults < portfolio algo_configs row < --comb-json CLI, all
        # resolved in _comb.config.resolve_comb. The algo_configs key is the
        # FT-LESS portfolio name (primary code) — a config is a
        # parametrization, the _ft{N} suffix is run identity only, and the
        # trainer writes trained comb rows under the same ft-less name;
        # old rows without a ``comb`` block get the champion defaults. The
        # full config rides the params dict (never a partial comb — the
        # loader merges shallowly).
        pf_config_name = portfolio_name(self.selection)
        db_params = None
        config_source = None
        if codes:
            db_params = await load_algo_config(
                self.conn, sec_type, codes[0], pf_config_name,
            )
            config_source = await load_config_source(
                self.conn, sec_type, codes[0], pf_config_name,
            )
        comb = resolve_comb(db_params, self.comb_overrides)
        params["comb"] = comb.model_dump()
        params["params_version"] = PARAMS_VERSION
        if db_params:
            # The trainer's Set A/B keys ride the SAME row as the comb:
            # apply them over the trading layer (mirroring the binary
            # path's load_params precedence — the tuned DB values beat
            # the STRATEGY_PARAMS defaults; CLI-owned keys like
            # buy_notional / fault_tolerance stay untouched).
            trained_keys = {
                key: db_params[key] for key in _TRAINED_PORTFOLIO_KEYS
                if key in db_params
            }
            params.update(trained_keys)
            if trained_keys:
                logger.info(f"    [algo_configs] applied trained keys from "
                      f"'{pf_config_name}' row: {trained_keys}")
        if config_source is not None:
            # Provenance: link the run to the algo_configs row the comb
            # was resolved from (trained row date / default / version).
            params["config_source"] = config_source.model_dump(mode="json")
        logger.info(f"    [comb] {pf_name}: enabled={comb.enabled} "
              f"gate={comb.regime_gate.enabled}(w{comb.regime_gate.window}) "
              f"weights={comb.weights.mode}@{comb.weights.window_rows} "
              f"u2={comb.quantile_sizing.enabled} u3={comb.vol_state.enabled} "
              f"u4={comb.equity_guard.enabled} "
              f"amp={comb.amplifier.enabled} "
              f"breadth={comb.breadth.enabled} "
              f"adhoc={comb.adhoc_actions.enabled}"
              f"(cap {comb.adhoc_actions.low_cap_lots}/"
              f"{comb.adhoc_actions.healthy_cap_lots})"
              + (" (CLI/DB override)" if db_params or self.comb_overrides else ""))

        await StrategyRunner(
            self.conn, force=self.force, seq_no=self.seq_no,
            dry_run=dry_run, t0=self.t0,
        ).run_one_sec_type(
            strategy_name=pf_name,
            sec_type=sec_type,
            codes=codes,
            params=params,
            fetch_signal_fn=self.collector.fetch_signal_data,
            backtest_fn=self.collector.run_backtest,
            daily_fn=self.collector.compute_daily_rows,
        )
        logger.info(f"    [Phase 2] portfolio '{pf_name}' backtest complete.")


# ---------------------------------------------------------------------------
# Delegating wrappers — lazy-facade import contract
# ---------------------------------------------------------------------------
async def run_sub_algos(
    pool,
    selection: Dict[str, float],
    *,
    sec_type: str,
    codes: List[str],
    trading_layer: dict,
    fault_tolerance: float = 0,
    force: bool,
    seq_no: Optional[int],
    dry_run: bool,
    t0: float,
) -> List[str]:
    """Delegating wrapper kept for the lazy-facade import contract —
    ``strategy.factors_and_algos`` resolves ``run_sub_algos`` via
    ``getattr(portfolio, name)`` (PEP 562 machinery that must stay
    untouched), so the module keeps the former pool-first signature.

    Constructs a :class:`PortfolioRunner` on the caller-supplied pool and
    runs Phase 1. ``conn`` / ``collector`` are Phase-2 state — not needed
    here (passed as None).
    """
    return await PortfolioRunner(
        conn=None, pool=pool, collector=None, selection=selection,
        trading_layer=trading_layer, fault_tolerance=fault_tolerance,
        force=force, seq_no=seq_no, t0=t0,
    ).run_sub_algos(sec_type=sec_type, codes=codes, dry_run=dry_run)


async def build_algo_portfolio(
    conn,
    collector,
    selection: Dict[str, float],
    *,
    sec_type: str,
    codes: List[str],
    trading_layer: dict,
    fault_tolerance: float = 0,
    comb_overrides: Optional[dict] = None,
    force: bool,
    seq_no: Optional[int],
    dry_run: bool,
    t0: float,
) -> None:
    """Delegating wrapper kept for the lazy-facade import contract (see
    :func:`run_sub_algos`).

    Constructs a :class:`PortfolioRunner` on the caller-supplied connection +
    collector and runs Phase 2. ``pool`` is Phase-1 state — not needed here
    (passed as None).
    """
    await PortfolioRunner(
        conn=conn, pool=None, collector=collector, selection=selection,
        trading_layer=trading_layer, fault_tolerance=fault_tolerance,
        comb_overrides=comb_overrides,
        force=force, seq_no=seq_no, t0=t0,
    ).build_algo_portfolio(sec_type=sec_type, codes=codes, dry_run=dry_run)


__all__ = [
    "portfolio_name",
    "blend_signal_confidence",
    "check_position_aware",
    "PortfolioRunner",
    "run_sub_algos",
    "build_algo_portfolio",
]
