"""CLI entry: ``python -m strategy.singleton_trading --algo <algo>``.

Pure ENTRY POINT. ``--algo`` selects a pluggable signal algo from
``strategy.factors_and_algos`` (default ``bollinger_bands``; also ``macd``,
``ma_spread``). The DB strategy identity
(``strategy_identity.strategy_name``) is the **algo name**, so each algo's
runs are stored and queried under their own name.

Two modes
---------
BINARY (single algo): ``--algo bollinger_bands``. The collector delegates
fetch/apply/backtest to that one algo. The strategy_name stored in the DB
is the algo name itself.

MIXED (weighted blend): ``--algo bollinger_bands:0.5,macd:0.5``. Two phases:
  1. Phase 1 — ``run_sub_algos``: each sub-algo runs INDEPENDENTLY on its
     own pooled connection (async gather), writing its own strategy_identity
     (strategy_name = algo_name) with skip-if-already-found.
  2. Phase 2 — ``build_algo_portfolio``: the collector (mixed mode) runs
     the blended backtest under a new ``portfolio:bb*0.5+macd*0.5``
     strategy_name.

After both phases, risks + forecast are computed for every strategy_name
(sub-algos + portfolio).

Per-(security, date-range) algo params are loaded from
``strategy.algo_configs`` (a default row is inserted on first run if none
exists); trading-layer keys (min_holding_period, buy_notional,
skip_final_liquidation) come from STRATEGY_PARAMS / CLI.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

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
from strategy._common.fetch import discover_available_codes  # noqa: E402

# Engine/runner-consumed keys that stay CLI-driven (NOT stored in the DB
# default algo_configs row). Everything else in the merged params comes from
# the algo's DEFAULT_PARAMS + the DB row.
_TRADING_LAYER_KEYS = ("min_holding_period", "buy_notional", "skip_final_liquidation")


def _parse_algo_arg(raw: str) -> dict:
    """Parse the --algo CLI value into ``{algo_name: weight}``.

    Accepted formats:
      - "bollinger_bands"               -> {"bollinger_bands": 1.0}  (binary)
      - "bollinger_bands:0.5,macd:0.5"  -> {"bollinger_bands": 0.5, "macd": 0.5}  (mixed)
      - "bollinger_bands:1,macd:2"      -> {"bollinger_bands": 1.0, "macd": 2.0}  (weights normalized later)

    Weights default to 1.0 when omitted (e.g. "bollinger_bands,macd" -> both 1.0).
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
        description="Algo-driven backtest runner (default: Bollinger Bands).",
    )
    ap.add_argument(
        "--algo", default=DEFAULT_ALGO,
        help=(
            f"Signal algo (registered in factors_and_algos). "
            f"Default: {DEFAULT_ALGO}. Binary: 'bollinger_bands'. "
            f"Mixed (weighted blend): 'bollinger_bands:0.5,macd:0.5'. "
            f"Also: macd, ma_spread."
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
        "--no-forecast", action="store_true",
        help="Skip the 1-month forecast (10 scenarios + child seqs). "
             "Forecast is on by default.",
    )
    args = ap.parse_args()

    selection = _parse_algo_arg(args.algo)
    is_mixed = len({n: w for n, w in selection.items() if w != 0}) > 1

    # Trading-layer params (shared across all algos + the portfolio).
    trading_layer = {
        "min_holding_period": STRATEGY_PARAMS["min_holding_period"],
        "buy_notional": args.buy_notional,
        "skip_final_liquidation": STRATEGY_PARAMS["skip_final_liquidation"],
    }

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
            strategy_name = algo_name
            collector = AlgoSignalCollector({algo_name: 1.0})

            params = dict(STRATEGY_PARAMS)
            params["buy_notional"] = args.buy_notional

            # DB-backed param loading (existing path).
            if codes_by_st:
                for st in sec_types:
                    for code in codes_by_st.get(st, []):
                        inserted = await ensure_default_config(
                            conn, strategy_name, st, code, strategy_name,
                        )
                        if inserted:
                            print(f"    [algo_configs] inserted default "
                                  f"{strategy_name} config for {st}/{code}", flush=True)
                primary_st = next(iter(codes_by_st))
                primary_code = codes_by_st[primary_st][0]
                tl = {k: params[k] for k in _TRADING_LAYER_KEYS if k in params}
                params = await load_params(
                    conn, strategy_name, primary_st, primary_code, strategy_name,
                    strategy_overrides=tl,
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
                if not args.no_forecast:
                    from strategy._1m_forcast.runner import run_forecast
                    for st in sec_types:
                        codes_for_st = (
                            codes_by_st[st] if codes_by_st and st in codes_by_st
                            else None
                        )
                        await run_forecast(
                            conn=conn, strategy_name=strategy_name,
                            sec_type=st, codes=codes_for_st,
                            force=args.force, dry_run=False,
                        )
                else:
                    print("  --no-forecast: skipping 1-month forecast.", flush=True)
        else:
            # ---- MIXED mode: sub-algos + portfolio ----
            from _common.db_commons import get_db_pool_async
            n_algos = sum(1 for w in selection.values() if w != 0)
            pool = await get_db_pool_async(
                min_size=1, max_size=min(n_algos, 4),
            )

            pf_name = portfolio_name(selection)
            print(f"\n=== MIXED mode ===\n  selection: {selection}\n  "
                  f"portfolio_name: {pf_name}\n  sub-algos to run: "
                  f"{[n for n, w in selection.items() if w != 0]}", flush=True)

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
                if not args.dry_run:
                    await run_sub_algos(
                        pool, selection,
                        sec_type=st, codes=codes,
                        trading_layer=trading_layer,
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
                    force=args.force, seq_no=args.seq_no,
                    dry_run=args.dry_run, t0=t0,
                )

                if not args.dry_run:
                    # Risks + forecast for every strategy_name (sub-algos + portfolio).
                    all_names = [n for n, w in selection.items() if w != 0] + [pf_name]
                    for sname in all_names:
                        print(f"\n  --- risks + forecast for '{sname}' [{st}] ---",
                              flush=True)
                        await compute_and_upsert_risks(
                            conn=conn, sec_types=[st],
                            codes_by_st={st: codes}, force=args.force,
                            strategy_name=sname,
                        )
                        if not args.no_forecast:
                            from strategy._1m_forcast.runner import run_forecast
                            await run_forecast(
                                conn=conn, strategy_name=sname,
                                sec_type=st, codes=codes,
                                force=args.force, dry_run=False,
                            )
                        else:
                            print(f"  --no-forecast: skipping forecast for {sname}.",
                                  flush=True)
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
    asyncio.run(main())
