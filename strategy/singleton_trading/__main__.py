"""CLI entry: ``python -m strategy.singleton_trading --algo <algo>``.

Pure ENTRY POINT. ``--algo`` selects a pluggable signal algo from
``strategy.factors_and_algos`` (default ``macd``). The DB strategy identity
(``strategy_identity.strategy_name``) is the **algo name**, so each algo's
runs are stored and queried under their own name.

Two modes
---------
BINARY (single family): ``--algo macd``. The collector delegates
fetch/apply/backtest to that one family's algo. The strategy_name stored
in the DB is the family name itself.

MIXED (equal blend — weighting REMOVED 2026-11-07, user-directed):
``--algo macd,mov_rsi``. Two phases:
  1. Phase 1 — ``run_sub_algos``: each family runs INDEPENDENTLY on its
     own pooled connection (async gather), writing its own strategy_identity
     (strategy_name = family name) with skip-if-already-found.
  2. Phase 2 — ``build_algo_portfolio``: the collector (mixed mode) runs
     the blended backtest under a new ``portfolio:macd+mov_rsi``
     strategy_name (equal weights — no weight suffixes).

After both phases, risks are computed for every strategy_name
(sub-algos + portfolio).

Per-(security, date-range) algo params are loaded from
``strategy.algo_configs`` (a default row is inserted on first run if none
exists); trading-layer keys (min_holding_period, buy_notional) come from STRATEGY_PARAMS / CLI.

The :class:`SingletonTradingStrategy` entry class (a
:class:`StrategyPipeline` subclass) owns the runtime lifecycle + DB
connection/pool lifecycle; the pipeline branches live in run().
Bootstrap-first layout: the runtime setup runs before this module's
pandas-importing strategy imports.
"""
from __future__ import annotations

import os
import sys

# Runtime bootstrap — pre-check → silence warnings → cudf.pandas hook →
# UTF-8 stdout. MUST run before the strategy imports below (fetch / algos
# / engine pull pandas; the hook must patch the pandas import first).
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from strategy._common.db import print_wall_time  # noqa: F401
from strategy._common.strategy_base import StrategyPipeline
from strategy._common.constants import ALL_SEC_TYPES, DEFAULT_BUY_NOTIONAL
from strategy._trading.constants import DEFAULT_FAULT_TOLERANCE
from strategy.singleton_trading import DEFAULT_ALGO, STRATEGY_PARAMS
from strategy._risks import RiskRunner
from strategy.factors_and_algos import (
    AlgoSignalCollector,
    ensure_default_config, load_config_source, load_params,
    portfolio_name,
)
# PortfolioRunner comes from the module directly — the lazy facade's
# __getattr__ machinery stays untouched.
from strategy.factors_and_algos.portfolio import PortfolioRunner
from strategy.factors_and_algos._algo.fault_tolerance import append_ft_suffix
from strategy._common.fetch import discover_available_codes

from _common.log_setup import setup_logging

logger = setup_logging("singleton_trading")

# Engine/runner-consumed keys that stay CLI-driven (NOT stored in the DB
# default algo_configs row). Everything else in the merged params comes from
# the algo's DEFAULT_PARAMS + the DB row.
_TRADING_LAYER_KEYS = ("min_holding_period", "buy_notional", "fault_tolerance")


def _parse_algo_arg(raw: str) -> dict:
    """Parse the --algo CLI value into ``{algo_name: 1.0}`` (NO weights).

    Accepted format: a comma list of signal family names —

      - "macd"            -> {"macd": 1.0}          (binary)
      - "macd,mov_rsi"    -> {"macd": 1.0, "mov_rsi": 1.0}  (mixed)

    Weighting was REMOVED (2026-11-07, user-directed "remove weighting
    from signals"): a mixed selection blends the picked-up events at
    EQUAL weight (the collector normalizes). A legacy ":weight" suffix
    ("macd:0.5") is tolerated and IGNORED (stripped) so stale callers
    don't crash. Names may be any analysis signal family (see
    ``registry.get_algo``).

    ORACLE is SOLO-ONLY (2026-11-21, user-directed "the optimization
    only optimizes on oracle only if on oracle mode, not to load other
    signals"): any multi-name selection carrying "oracle" is rejected —
    the hindsight replay never loads or blends other families (the UI's
    oracle mode toggle already forces this; this guards the CLI).
    """
    selection: dict = {}
    for part in raw.split(","):
        name = part.split(":", 1)[0].strip()
        if not name:
            continue
        selection[name] = 1.0
    if not selection:
        raise ValueError(f"--algo '{raw}' parsed to an empty selection")
    if "oracle" in selection and len(selection) > 1:
        raise SystemExit(
            "oracle is solo-only (oracle mode): the hindsight replay "
            "never loads other signal families — "
            f"got '--algo {raw}'"
        )
    return selection


class SingletonTradingStrategy(StrategyPipeline):
    """``python -m strategy.singleton_trading`` — algo backtest runner.

    The mixed-mode pool (one connection per sub-algo, capped at 4) is
    opened by the StrategyPipeline amain() template when apply_args()
    sets ``pool_size``; binary mode runs on the single connection.
    """

    component = "singleton_trading"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--algo", default=DEFAULT_ALGO,
            help=(
                f"Analysis signal family(ies) whose recorded events drive "
                f"the backtest (picked up from analysis_signals.history_"
                f"signals). Default: {DEFAULT_ALGO}. Binary: 'macd'. "
                f"Mixed (equal blend): 'macd,mov_rsi'."
            ),
        )
        parser.add_argument("--sec-type", choices=("index", "etf", "stock"), default=None)
        parser.add_argument("--codes", nargs="+", default=None)
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--buy-notional", type=float, default=DEFAULT_BUY_NOTIONAL)
        parser.add_argument("--seq-no", type=int, default=None)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--fault-tolerance", type=float, default=DEFAULT_FAULT_TOLERANCE,
            help="Fault tolerance percentage (0-20), pinned to 10 by default "
                 "(see strategy/_trading/constants.py). When >0, BUY fills are "
                 "adversely shifted by ft%% of |delta_close| (engine stress) "
                 "and a two-pass stress annotation runs: baseline decisions, "
                 "then OHLC perturbed on those dates, indicators recomputed, "
                 "and the algo re-run on stressed data. Strategy name gets "
                 "_ft{N} suffix.",
        )
        parser.add_argument(
            "--comb-json", default=None,
            help=(
                "MIXED-mode comb-config overrides (JSON), merged over the "
                "portfolio's algo_configs row and the champion defaults — "
                'e.g. \'{"enabled": false}\' to disable the comb stack, or '
                '\'{"weights": {"mode": "mean_bar", "bar": 0.0025}}\'. '
                "See strategy/factors_and_algos/_comb/config.py."
            ),
        )

    def apply_args(self) -> None:
        args = self.args

        self.ft = max(0.0, min(20.0, args.fault_tolerance))
        self.selection = _parse_algo_arg(args.algo)
        self.is_mixed = len(
            {n: w for n, w in self.selection.items() if w != 0}
        ) > 1

        # Trading-layer params (shared across all algos + the portfolio).
        # fault_tolerance is threaded into params so the two-pass runner in
        # AlgoBase.run_backtest / AlgoSignalCollector.run_backtest picks it up.
        self.trading_layer = {
            "min_holding_period": STRATEGY_PARAMS["min_holding_period"],
            "buy_notional": args.buy_notional,
        }
        if self.ft > 0:
            self.trading_layer["fault_tolerance"] = self.ft

        self.discovery = args.all or not args.codes
        self.sec_types = (args.sec_type,) if args.sec_type else ALL_SEC_TYPES
        self.codes_by_st = (
            {args.sec_type: list(args.codes)}
            if args.codes and not args.all and args.sec_type else None
        )

        # MIXED-mode comb overrides (--comb-json): validated NOW via the
        # comb schema so an invalid override fails fast at startup, not
        # mid-run. None keeps the champion defaults / DB row.
        self.comb_overrides = None
        if args.comb_json:
            import json as _json
            from strategy.factors_and_algos._comb import CombParams
            try:
                raw = _json.loads(args.comb_json)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"--comb-json is not valid JSON: {exc}"
                ) from exc
            if not isinstance(raw, dict):
                raise ValueError("--comb-json must be a JSON object")
            # Validate through the strict schema (CLI is not a stale row —
            # invalid values SHOULD fail loudly here).
            CombParams.model_validate(raw)
            self.comb_overrides = raw

        # Mixed mode only: one pooled connection per non-zero sub-algo.
        self.pool_size = (
            min(sum(1 for w in self.selection.values() if w != 0), 4)
            if self.is_mixed else None
        )

    async def run(self) -> None:
        conn = self.conn
        args = self.args
        ft = self.ft
        selection = self.selection
        sec_types = self.sec_types
        codes_by_st = self.codes_by_st
        t0 = self.t0

        if not self.is_mixed:
            # ---- BINARY mode: single algo (existing path) ----
            algo_name = next(iter(selection))
            strategy_name = append_ft_suffix(algo_name, ft)
            collector = AlgoSignalCollector({algo_name: 1.0})

            # Trading-layer defaults + the algo's DEFAULT_PARAMS. The
            # codes_by_st branch below re-merges DB algo_configs on top;
            # without an explicit code list there is no (sec_type, code) to
            # load a config for, so the algo defaults ARE the params (the
            # algo's model params were retired with the 2026-11-07
            # signals-picked-up refactor — DEFAULT_PARAMS now documents
            # the analysis_signals source only).
            from strategy.factors_and_algos import get_algo
            params = {
                **get_algo(algo_name).DEFAULT_PARAMS,
                **dict(STRATEGY_PARAMS),
            }
            params["buy_notional"] = args.buy_notional
            if ft > 0:
                params["fault_tolerance"] = ft

            # DB-backed param loading (existing path). The algo_configs key
            # is the FT-LESS algo name — a config is a parametrization, the
            # _ft{N} suffix is run identity only. (Phase 1 of the mixed
            # branch follows the same rule; the trainer also writes under
            # the ft-less name, so trained rows load at ANY fault tolerance.)
            if codes_by_st:
                for st in sec_types:
                    for code in codes_by_st.get(st, []):
                        inserted = await ensure_default_config(
                            conn, algo_name, st, code, algo_name,
                        )
                        if inserted:
                            logger.info(f"    [algo_configs] inserted default "
                                  f"{algo_name} config for {st}/{code}")
                primary_st = next(iter(codes_by_st))
                primary_code = codes_by_st[primary_st][0]
                # min_holding_period is NOT forced here: when the optimizer
                # (_optm_engine) tuned it, the DB algo_configs row wins; the
                # hardcoded STRATEGY_PARAMS value is only the fallback.
                tl = {
                    k: params[k] for k in _TRADING_LAYER_KEYS
                    if k in params and k != "min_holding_period"
                }
                params = await load_params(
                    conn, algo_name, primary_st, primary_code, algo_name,
                    strategy_overrides=tl,
                )
                params.setdefault(
                    "min_holding_period",
                    STRATEGY_PARAMS["min_holding_period"],
                )
                # Provenance: link the run to the algo_configs row it
                # actually loaded (trained row date / default / version).
                source = await load_config_source(
                    conn, primary_st, primary_code, algo_name,
                )
                if source is not None:
                    params["config_source"] = source.model_dump(mode="json")
                logger.info(f"    [algo_configs] loaded {algo_name} params from DB "
                      f"for {primary_st}/{primary_code}")

            from strategy._common.runner import StrategyRunner

            await StrategyRunner(
                conn, force=args.force, seq_no=args.seq_no,
                dry_run=args.dry_run, t0=t0,
            ).discover_and_run(
                strategy_name=strategy_name,
                sec_types=list(sec_types), codes_by_st=codes_by_st,
                params=params, fetch_signal_fn=collector.fetch_signal_data,
                backtest_fn=collector.run_backtest, daily_fn=collector.compute_daily_rows,
                discovery=self.discovery,
            )
            if not args.dry_run:
                await RiskRunner(
                    conn, force=args.force,
                ).compute_and_upsert_risks(
                    sec_types=list(sec_types),
                    codes_by_st=codes_by_st,
                    strategy_name=strategy_name,
                )
        else:
            # ---- MIXED mode: sub-algos + portfolio ----
            pf_name = portfolio_name(selection, fault_tolerance=ft)
            logger.info(f"\n=== MIXED mode ===\n  selection: {selection}\n  "
                  f"portfolio_name: {pf_name}\n  sub-algos to run: "
                  f"{[n for n, w in selection.items() if w != 0]}")
            if ft > 0:
                logger.info(f"  fault_tolerance: {ft}% (strategy names get _ft{int(round(ft))} suffix)")

            collector = AlgoSignalCollector(selection)  # mixed-mode collector

            # One conn/pool-scoped portfolio orchestrator for the whole mixed
            # run: pool drives Phase 1 (sub-algos), conn + collector Phase 2
            # (blended portfolio). Everything ctor-held here is identical at
            # both call sites across the per-sec_type loop; dry_run /
            # sec_type / codes stay per-call.
            portfolio = PortfolioRunner(
                conn, self.pool, collector, selection,
                trading_layer=self.trading_layer, fault_tolerance=ft,
                comb_overrides=self.comb_overrides,
                force=args.force, seq_no=args.seq_no, t0=t0,
            )

            for st in sec_types:
                # Resolve codes for this sec_type.
                if codes_by_st and st in codes_by_st:
                    codes = codes_by_st[st]
                elif self.discovery:
                    logger.info(f"\n>>> Discovering available codes for sec_type={st} "
                          f"from analysis.mov_ave_spreads_detail...")
                    codes = await discover_available_codes(conn, st)
                    logger.info(f"    -> found {len(codes)} code(s)")
                    if not codes:
                        continue
                else:
                    continue

                # Phase 1: async-run sub-algos independently (pooled).
                # Each sub-algo gets the _ft{N} suffix in its strategy_name
                # so FT and non-FT runs coexist in the DB.
                if not args.dry_run:
                    await portfolio.run_sub_algos(
                        sec_type=st, codes=codes,
                        dry_run=False,
                    )
                else:
                    logger.info(f"\n[Phase 1] --dry-run: skipping sub-algo runs "
                          f"for {st}.")

                # Phase 2: portfolio (blended backtest).
                await portfolio.build_algo_portfolio(
                    sec_type=st, codes=codes,
                    dry_run=args.dry_run,
                )

                if not args.dry_run:
                    # Risks for every strategy_name (sub-algos + portfolio).
                    all_names = [
                        append_ft_suffix(n, ft) for n, w in selection.items() if w != 0
                    ] + [pf_name]
                    for sname in all_names:
                        logger.info(f"\n  --- risks for '{sname}' [{st}] ---")
                        await RiskRunner(
                            conn, force=args.force,
                        ).compute_and_upsert_risks(
                            sec_types=[st],
                            codes_by_st={st: codes},
                            strategy_name=sname,
                        )


if __name__ == "__main__":
    SingletonTradingStrategy().execute()
