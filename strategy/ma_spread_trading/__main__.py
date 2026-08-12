"""Entry point for the MA-spread backtest strategy.

Run via ``python -m strategy.ma_spread_trading``.

Default mode discovers ALL available codes in analysis.mov_ave_spreads_detail
for EACH sec_type (index, etf, stock) and backtests them in a single run per
sec_type, then computes internal risk metrics for every run. Use --sec-type /
--codes to restrict to a single universe or code. Use --all to force
discovery mode (the default when --codes is omitted).

The batched fetch→backtest→upsert loop lives in strategy._common.runner; this
module supplies the MA-spread run_backtest callable (which wires the
signal layer strategy._signal → the execution layer strategy._trading),
the signal layer's fetch_signal_data, parses CLI args, and invokes the
risk pipeline post-backtest.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# Ensure project root is on sys.path so ``_common`` / ``strategy`` import.
sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

from strategy._common.db import (  # noqa: E402
    setup_utf8_stdout, get_db_or_exit, print_wall_time,
)
from strategy._common.constants import (  # noqa: E402
    ALL_SEC_TYPES, DEFAULT_CODES, DEFAULT_BUY_NOTIONAL,
)
from strategy._common.runner import discover_and_run  # noqa: E402

setup_utf8_stdout()

from strategy.ma_spread_trading.config import (  # noqa: E402
    STRATEGY_NAME, STRATEGY_PARAMS,
)
from strategy._signal import fetch_signal_data  # noqa: E402
from strategy.ma_spread_trading.backtest import run_backtest, compute_daily_rows  # noqa: E402
from strategy._risks import compute_and_upsert_risks  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="MA-spread crossover backtest strategy. "
                    "By default discovers all available codes in "
                    "analysis.mov_ave_spreads_detail for each sec_type."
    )
    ap.add_argument("--sec-type", default=None,
                    choices=("index", "etf", "stock"),
                    help="Restrict to one sec_type. If omitted, all three "
                         "(index/etf/stock) are run in discovery mode.")
    ap.add_argument("--codes", nargs="+", default=None,
                    help="Ticker(s) to backtest. If omitted, --all discovery "
                         "is used (default behavior).")
    ap.add_argument("--all", action="store_true",
                    help="Force discovery mode: backtest every code available "
                         "in analysis.mov_ave_spreads_detail for the sec_type(s).")
    ap.add_argument("--buy-notional", type=float, default=DEFAULT_BUY_NOTIONAL,
                    help=f"Yuan deployed per BUY at confidence=100 "
                         f"(default: {DEFAULT_BUY_NOTIONAL:,.0f}). No fixed "
                         f"capital budget — BUYs accumulate freely.")
    ap.add_argument("--seq-no", type=int, default=None,
                    help="Force a specific seq_no (default: next available).")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite an existing (strategy_name, seq_no) run.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run the backtest but do NOT write to the DB.")
    args = ap.parse_args()

    # Discovery mode: --all OR no --codes given.
    discovery = args.all or not args.codes
    sec_types = (args.sec_type,) if args.sec_type else ALL_SEC_TYPES

    # Explicit codes: require --sec-type (no silent default to a single
    # sec_type — that was the old DEFAULT_SEC_TYPE='index' skip logic that
    # prevented etf/stock from being backtested when callers forgot to pass
    # --sec-type). When --codes is given without --sec-type, fall through to
    # discovery mode for ALL sec_types so every universe is considered.
    if args.codes and not args.all and args.sec_type:
        codes_by_st = {args.sec_type: list(args.codes)}
    else:
        codes_by_st = None  # discovered per sec_type below

    params = dict(STRATEGY_PARAMS)
    params["buy_notional"] = args.buy_notional

    t0 = time.time()
    conn = await get_db_or_exit()
    try:
        await discover_and_run(
            conn=conn,
            strategy_name=STRATEGY_NAME,
            sec_types=list(sec_types),
            codes_by_st=codes_by_st,
            params=params,
            fetch_signal_fn=fetch_signal_data,
            backtest_fn=run_backtest,
            daily_fn=compute_daily_rows,
            force=args.force,
            seq_no=args.seq_no,
            dry_run=args.dry_run,
            discovery=discovery,
        )

        # Compute + upsert internal risk metrics for the same sec_types/codes
        # just backtested. Skipped on --dry-run (no DB rows to read).
        if not args.dry_run:
            await compute_and_upsert_risks(
                conn=conn,
                sec_types=list(sec_types),
                codes_by_st=codes_by_st,
                force=args.force,
            )
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
