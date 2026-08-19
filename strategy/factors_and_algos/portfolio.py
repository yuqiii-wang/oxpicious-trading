"""Portfolio builder — async run sub-algos + weight-blend into a portfolio.

This module orchestrates the MIXED mode of :class:`AlgoSignalCollector`:

  1. :func:`run_sub_algos` (Phase 1): async-gather each sub-algo's full
     backtest. Each sub-algo runs INDEPENDENTLY via the shared runner, writing
     its own ``strategy_identity`` row (``strategy_name = algo_name``) with
     skip-if-already-found (idempotent). Sub-algos with weight 0 are skipped.
     A connection POOL is used so the sub-algos run concurrently — a single
     asyncpg connection serializes queries.

  2. :func:`build_algo_portfolio` (Phase 2): after sub-algos finish, run the
     PORTFOLIO backtest using the collector's blended fetch+apply+engine.
     The portfolio is a NEW ``strategy_identity`` (``strategy_name =
     portfolio_name(selection)``) that combines sub-algo signals by weight
     into one consolidated ``signal_confidence``, then runs the same
     signal-agnostic engine. The portfolio's OHLC period (start_date /
     end_date on strategy_identity) is the union of sub-algo periods.

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


# ---------------------------------------------------------------------------
# Portfolio naming
# ---------------------------------------------------------------------------


def portfolio_name(
    selection: Dict[str, float],
    fault_tolerance: float = 0,
) -> str:
    """Build the portfolio strategy_name from the algo->weight selection.

    e.g. {"macd": 0.5} -> "portfolio:macd*0.5"
    With fault_tolerance=10 -> "portfolio:macd*0.5_ft10"

    Algos with weight 0 are omitted from the name. The name is stable
    (sorted by algo name) so the same selection always maps to the same
    strategy_name — this is what makes the portfolio idempotent (skip-if-
    already-found in strategy_identity).
    """
    parts = []
    for name in sorted(selection):
        weight = selection[name]
        if weight == 0:
            continue
        parts.append(f"{short_name(name)}*{weight:g}")
    if not parts:
        base = "portfolio:empty"
    else:
        base = "portfolio:" + "+".join(parts)
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
# Phase 1: run sub-algos independently (async, pooled)
# ---------------------------------------------------------------------------
async def _run_one_sub_algo(
    pool,
    *,
    algo_name: str,
    sec_type: str,
    codes: List[str],
    trading_layer: dict,
    fault_tolerance: float = 0,
    force: bool,
    seq_no: Optional[int],
    dry_run: bool,
    t0: float,
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
    from strategy._common.runner import run_one_sec_type
    from strategy.factors_and_algos import (
        AlgoSignalCollector, ensure_default_config, load_params,
    )

    strategy_name = append_ft_suffix(algo_name, fault_tolerance)

    async with pool.acquire() as conn:
        # Ensure a default algo_configs row exists for each code (so
        # load_params finds something). Idempotent.
        for code in codes:
            inserted = await ensure_default_config(
                conn, algo_name, sec_type, code, algo_name,
            )
            if inserted:
                print(f"    [algo_configs] inserted default {algo_name} "
                      f"config for {sec_type}/{code}", flush=True)
        # Load merged params (algo DEFAULT < DB < trading-layer overrides).
        # Uses the FIRST code as the config source (params are per-algo, not
        # per-code — they share the same algo_configs strategy_name).
        primary_code = codes[0] if codes else ""
        params = await load_params(
            conn, algo_name, sec_type, primary_code, algo_name,
            strategy_overrides=trading_layer,
        )

        # Binary collector for this one algo.
        collector = AlgoSignalCollector({algo_name: 1.0})
        await run_one_sec_type(
            conn=conn,
            strategy_name=strategy_name,
            sec_type=sec_type,
            codes=codes,
            params=params,
            fetch_signal_fn=collector.fetch_signal_data,
            backtest_fn=collector.run_backtest,
            daily_fn=collector.compute_daily_rows,
            force=force,
            seq_no=seq_no,
            dry_run=dry_run,
            t0=t0,
        )
    return algo_name


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
    """Phase 1: async-gather each sub-algo's full backtest.

    Each sub-algo (weight != 0) runs independently on its own pooled
    connection via :func:`_run_one_sub_algo`. ``asyncio.gather`` runs them
    concurrently. Returns the list of algo_names that ran.

    ``trading_layer`` carries the engine-consumed keys (min_holding_period,
    buy_notional, skip_final_liquidation) shared across all sub-algos.
    ``fault_tolerance`` > 0 appends ``_ft{N}`` to each sub-algo's
    strategy_name (so FT and non-FT runs coexist in the DB).
    """
    check_position_aware(selection)

    tasks = []
    ran_names: List[str] = []
    for algo_name, weight in selection.items():
        if weight == 0:
            continue
        ran_names.append(algo_name)
        tasks.append(
            _run_one_sub_algo(
                pool,
                algo_name=algo_name,
                sec_type=sec_type,
                codes=codes,
                trading_layer=trading_layer,
                fault_tolerance=fault_tolerance,
                force=force,
                seq_no=seq_no,
                dry_run=dry_run,
                t0=t0,
            )
        )
    if not tasks:
        print("  [portfolio] no sub-algos with non-zero weight; skipping Phase 1.",
              flush=True)
        return []

    print(f"\n[Phase 1] Running {len(tasks)} sub-algo(s) concurrently: "
          f"{ran_names}", flush=True)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for name, res in zip(ran_names, results):
        if isinstance(res, Exception):
            print(f"    [Phase 1] {name}: ERROR {type(res).__name__}: {res}",
                  flush=True)
        else:
            print(f"    [Phase 1] {name}: done", flush=True)
    return ran_names


# ---------------------------------------------------------------------------
# Phase 2: build the portfolio (blended backtest)
# ---------------------------------------------------------------------------
async def build_algo_portfolio(
    conn,
    collector,
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
) -> None:
    """Phase 2: run the portfolio backtest (blended signals).

    The portfolio uses the collector's MIXED-mode fetch (union of per-algo
    data) + apply (weight-blend) + the same signal-agnostic engine. The
    strategy_name stored in strategy_identity is
    ``portfolio_name(selection, fault_tolerance)`` — a stable name derived
    from the algo->weight map (plus ``_ft{N}`` suffix when FT > 0) so the
    portfolio is idempotent (skip-if-already-found).

    After the backtest, risks + forecast are computed for the portfolio
    (same as a standalone algo run) — handled by the caller (__main__.py)
    to keep this module focused on the backtest itself.
    """
    from strategy._common.runner import run_one_sec_type

    pf_name = portfolio_name(selection, fault_tolerance=fault_tolerance)
    print(f"\n[Phase 2] Building portfolio '{pf_name}' "
          f"(blended backtest over {len(codes)} code(s))...", flush=True)

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
    params = dict(trading_layer)

    await run_one_sec_type(
        conn=conn,
        strategy_name=pf_name,
        sec_type=sec_type,
        codes=codes,
        params=params,
        fetch_signal_fn=collector.fetch_signal_data,
        backtest_fn=collector.run_backtest,
        daily_fn=collector.compute_daily_rows,
        force=force,
        seq_no=seq_no,
        dry_run=dry_run,
        t0=t0,
    )
    print(f"    [Phase 2] portfolio '{pf_name}' backtest complete.", flush=True)


__all__ = [
    "portfolio_name",
    "blend_signal_confidence",
    "check_position_aware",
    "run_sub_algos",
    "build_algo_portfolio",
]
