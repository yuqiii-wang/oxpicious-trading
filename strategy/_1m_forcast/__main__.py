"""CLI entry: ``python -m strategy._1m_forcast``.

Computes the 1-month forward sell-confidence forecast for existing
``singleton_trading`` runs. Discovers seq_ids per sec_type and, for each
run that ends with an open position, projects the 11 scenarios
(10 mirror/flip/random curves + 1 computed mean) and upserts them to
``strategy.forecast_1m``.

For each of the 10 display scenarios, a CHILD seq is created under the
parent backtest seq (strategy_identity.parent_seq_id + scenario). Each
child seq carries a full copy of the parent's actual decisions + that
scenario's 20 forecast sells, plus its own strategy_daily +
strategy_results + risk rows. The UI can then switch between scenarios
via a dropdown, and each scenario reflects its own risk/return profile.
The computed mean is NOT persisted as decisions — it only drives the
forecast_1m rows for the UI mean line.

This module is now a thin CLI wrapper around ``runner.run_forecast``,
which is also called from ``singleton_trading.__main__`` so the forecast
runs embedded in the backtest pipeline.

Usage:
    python -m strategy._1m_forcast --sec-type index             # all index runs
    python -m strategy._1m_forcast --sec-type index --codes 000036 000680
    python -m strategy._1m_forcast --seq-id 2906
    python -m strategy._1m_forcast --sec-type index --dry-run
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
from strategy._common.constants import ALL_SEC_TYPES, DEFAULT_SEC_TYPE  # noqa: E402
from strategy._1m_forcast import STRATEGY_NAME  # noqa: E402
from strategy._1m_forcast.runner import run_forecast  # noqa: E402

setup_utf8_stdout()


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="1-month forward sell-confidence forecast (10 mirror/flip/random curves + mean).",
    )
    ap.add_argument("--sec-type", choices=ALL_SEC_TYPES, default=DEFAULT_SEC_TYPE)
    ap.add_argument("--codes", nargs="+", default=None)
    ap.add_argument("--all", action="store_true",
                    help="Forecast every seq for the strategy+sec_type (default).")
    ap.add_argument("--seq-id", type=int, default=None,
                    help="Forecast a single strategy_seq by seq_id.")
    ap.add_argument("--strategy-name", default=STRATEGY_NAME,
                    help="Backtest strategy whose runs to forecast (default: singleton_trading).")
    ap.add_argument("--force", action="store_true",
                    help="Delete existing forecast rows for the (seq_id, forecast_date) first.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    conn = await get_db_or_exit()
    try:
        await run_forecast(
            conn,
            strategy_name=args.strategy_name,
            sec_type=args.sec_type,
            codes=args.codes,
            seq_id=args.seq_id,
            force=args.force,
            dry_run=args.dry_run,
        )
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
