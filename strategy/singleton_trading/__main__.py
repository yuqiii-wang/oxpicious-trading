"""CLI entry: ``python -m strategy.singleton_trading --algo <algo>``.

Pure ENTRY POINT. ``--algo`` selects a pluggable signal algo from
``strategy.factors_and_algos`` (default ``macd``). The DB strategy identity
(``strategy_identity.strategy_name``) is the **algo name**, so each algo's
runs are stored and queried under their own name.

Two modes
---------
BINARY (single algo): ``--algo macd``. The collector delegates
fetch/apply/backtest to that one algo. The strategy_name stored in the DB
is the algo name itself.

MIXED (weighted blend): ``--algo macd:0.5,bb:0.5``. Two phases:
  1. Phase 1 — ``run_sub_algos``: each sub-algo runs INDEPENDENTLY on its
     own pooled connection (async gather), writing its own strategy_identity
     (strategy_name = algo_name) with skip-if-already-found.
  2. Phase 2 — ``build_algo_portfolio``: the collector (mixed mode) runs
     the blended backtest under a new ``portfolio:macd*0.5``
     strategy_name.

After both phases, risks are computed for every strategy_name
(sub-algos + portfolio).

Per-(security, date-range) algo params are loaded from
``strategy.algo_configs`` (a default row is inserted on first run if none
exists); trading-layer keys (min_holding_period, buy_notional) come from STRATEGY_PARAMS / CLI.
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

# GPU decision FIRST — cudf.pandas must patch the pandas import before
# any module below (fetch / algos / engine) pulls pandas in. Shared
# util in _common.df_utils; its exports are lazy so this import stays
# pandas-free. Run Strategy signal math then transparently runs on the
# GPU when the shared detector finds a working CUDA device.
from _common.df_utils import maybe_enable_cudf_pandas  # noqa: E402

_GPU_ON, _GPU_WHY = maybe_enable_cudf_pandas("auto")
print(f"[gpu] {_GPU_WHY}", flush=True)

from strategy._common.db import setup_utf8_stdout, get_db_or_exit, print_wall_time  # noqa: E402
from strategy._common.constants import ALL_SEC_TYPES, DEFAULT_BUY_NOTIONAL  # noqa: E402
from strategy._common.runner import discover_and_run  # noqa: E402

setup_utf8_stdout()

from strategy.singleton_trading import DEFAULT_ALGO, STRATEGY_PARAMS  # noqa: E402
from strategy._risks import compute_and_upsert_risks  # noqa: E402
from strategy.factors_and_algos import (  # noqa: E402
    AlgoSignalCollector,
    ensure_default_config, load_params,
    portfolio_name, run_sub_algos, build_algo_portfolio,
)
from strategy.factors_and_algos._algo.fault_tolerance import append_ft_suffix  # noqa: E402
from strategy._common.fetch import discover_available_codes  # noqa: E402

# Engine/runner-consumed keys that stay CLI-driven (NOT stored in the DB
# default algo_configs row). Everything else in the merged params comes from
# the algo's DEFAULT_PARAMS + the DB row.
_TRADING_LAYER_KEYS = ("min_holding_period", "buy_notional", "fault_tolerance")


def _parse_algo_arg(raw: str) -> dict:
    """Parse the --algo CLI value into ``{algo_name: weight}``.

    Accepted formats:
      - "macd"                        -> {"macd": 1.0}  (binary)
      - "macd:0.5,bb:0.5"            -> {"macd": 0.5, "bb": 0.5}  (mixed)
      - "macd:1,bb:2"                -> {"macd": 1.0, "bb": 2.0}  (weights normalized later)

    Weights default to 1.0 when omitted (e.g. "macd,bb" -> both 1.0).
    """
    selection: dict = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            name, w = part.split(":", 1)
            selection[name.strip()] = float(w.strip())
        else:
            selection[part] = 1.0
    if not selection:
        raise ValueError(f"--algo '{raw}' parsed to an empty selection")
    return selection


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Algo-driven backtest runner (default: MACD).",
    )
    ap.add_argument(
        "--algo", default=DEFAULT_ALGO,
        help=(
            f"Signal algo (registered in factors_and_algos). "
            f"Default: {DEFAULT_ALGO}. Binary: 'macd'. "
            f"Mixed (weighted blend): 'macd:0.5,bb:0.5'.",
        ),
    )
    ap.add_argument("--sec-type", choices=("index", "etf", "stock"), default=None)
    ap.add_argument("--codes", nargs="+", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--buy-notional", type=float, default=DEFAULT_BUY_NOTIONAL)
    ap.add_argument("--seq-no", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--fault-tolerance", type=float, default=0,
        help="Fault tolerance percentage (0-20). When >0, runs a two-pass "
             "stress test: baseline run finds decision dates, then OHLC is "
             "adversely perturbed on those dates (BUY up, SELL down) by "
             "ft%% of |delta_close|, indicators are recomputed, and the "
             "algo re-runs on stressed data. Strategy name gets _ft{N} suffix.",
    )
    args = ap.parse_args()

    ft = max(0.0, min(20.0, args.fault_tolerance))
    selection = _parse_algo_arg(args.algo)
    is_mixed = len({n: w for n, w in selection.items() if w != 0}) > 1

    # Trading-layer params (shared across all algos + the portfolio).
    # fault_tolerance is threaded into params so the two-pass runner in
    # AlgoBase.run_backtest / AlgoSignalCollector.run_backtest picks it up.
    trading_layer = {
        "min_holding_period": STRATEGY_PARAMS["min_holding_period"],
        "buy_notional": args.buy_notional,
    }
    if ft > 0:
        trading_layer["fault_tolerance"] = ft

    discovery = args.all or not args.codes
    sec_types = (args.sec_type,) if args.sec_type else ALL_SEC_TYPES
    codes_by_st = (
        {args.sec_type: list(args.codes)}
        if args.codes and not args.all and args.sec_type else None
    )

    t0 = time.time()
    conn = await get_db_or_exit()
    pool = None
    try:
        if not is_mixed:
            # ---- BINARY mode: single algo (existing path) ----
            algo_name = next(iter(selection))
            strategy_name = append_ft_suffix(algo_name, ft)
            collector = AlgoSignalCollector({algo_name: 1.0})

            params = dict(STRATEGY_PARAMS)
            params["buy_notional"] = args.buy_notional
            if ft > 0:
                params["fault_tolerance"] = ft

            # DB-backed param loading (existing path).
            if codes_by_st:
                for st in sec_types:
                    for code in codes_by_st.get(st, []):
                        inserted = await ensure_default_config(
                            conn, algo_name, st, code, strategy_name,
                        )
                        if inserted:
                            print(f"    [algo_configs] inserted default "
                                  f"{strategy_name} config for {st}/{code}", flush=True)
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
                    conn, algo_name, primary_st, primary_code, strategy_name,
                    strategy_overrides=tl,
                )
                params.setdefault(
                    "min_holding_period",
                    STRATEGY_PARAMS["min_holding_period"],
                )
                print(f"    [algo_configs] loaded {strategy_name} params from DB "
                      f"for {primary_st}/{primary_code}", flush=True)

            await discover_and_run(
                conn=conn, strategy_name=strategy_name,
                sec_types=list(sec_types), codes_by_st=codes_by_st,
                params=params, fetch_signal_fn=collector.fetch_signal_data,
                backtest_fn=collector.run_backtest, daily_fn=collector.compute_daily_rows,
                force=args.force, seq_no=args.seq_no,
                dry_run=args.dry_run, discovery=discovery,
            )
            if not args.dry_run:
                await compute_and_upsert_risks(
                    conn=conn, sec_types=list(sec_types),
                    codes_by_st=codes_by_st, force=args.force,
                    strategy_name=strategy_name,
                )
        else:
            # ---- MIXED mode: sub-algos + portfolio ----
            from _common.db_commons import get_db_pool_async
            n_algos = sum(1 for w in selection.values() if w != 0)
            pool = await get_db_pool_async(
                min_size=1, max_size=min(n_algos, 4),
            )

            pf_name = portfolio_name(selection, fault_tolerance=ft)
            print(f"\n=== MIXED mode ===\n  selection: {selection}\n  "
                  f"portfolio_name: {pf_name}\n  sub-algos to run: "
                  f"{[n for n, w in selection.items() if w != 0]}", flush=True)
            if ft > 0:
                print(f"  fault_tolerance: {ft}% (strategy names get _ft{int(round(ft))} suffix)",
                      flush=True)

            collector = AlgoSignalCollector(selection)  # mixed-mode collector

            for st in sec_types:
                # Resolve codes for this sec_type.
                if codes_by_st and st in codes_by_st:
                    codes = codes_by_st[st]
                elif discovery:
                    print(f"\n>>> Discovering available codes for sec_type={st} "
                          f"from analysis.mov_ave_spreads_detail...", flush=True)
                    codes = await discover_available_codes(conn, st)
                    print(f"    -> found {len(codes)} code(s)", flush=True)
                    if not codes:
                        continue
                else:
                    continue

                # Phase 1: async-run sub-algos independently (pooled).
                # Each sub-algo gets the _ft{N} suffix in its strategy_name
                # so FT and non-FT runs coexist in the DB.
                if not args.dry_run:
                    await run_sub_algos(
                        pool, selection,
                        sec_type=st, codes=codes,
                        trading_layer=trading_layer,
                        fault_tolerance=ft,
                        force=args.force, seq_no=args.seq_no,
                        dry_run=False, t0=t0,
                    )
                else:
                    print(f"\n[Phase 1] --dry-run: skipping sub-algo runs "
                          f"for {st}.", flush=True)

                # Phase 2: portfolio (blended backtest).
                await build_algo_portfolio(
                    conn, collector, selection,
                    sec_type=st, codes=codes,
                    trading_layer=trading_layer,
                    fault_tolerance=ft,
                    force=args.force, seq_no=args.seq_no,
                    dry_run=args.dry_run, t0=t0,
                )

                if not args.dry_run:
                    # Risks for every strategy_name (sub-algos + portfolio).
                    all_names = [
                        append_ft_suffix(n, ft) for n, w in selection.items() if w != 0
                    ] + [pf_name]
                    for sname in all_names:
                        print(f"\n  --- risks for '{sname}' [{st}] ---",
                              flush=True)
                        await compute_and_upsert_risks(
                            conn=conn, sec_types=[st],
                            codes_by_st={st: codes}, force=args.force,
                            strategy_name=sname,
                        )
    finally:
        if pool is not None:
            try:
                await asyncio.wait_for(pool.close(), timeout=10)
            except (asyncio.TimeoutError, Exception):
                pass
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass

    print_wall_time(t0)


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
