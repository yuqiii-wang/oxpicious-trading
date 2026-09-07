"""Entry point for ``python -m builds.market_hypes``.

Thin CLI wrapper around ``run_build`` (runner.py). Populates
stats.mov_ave_market_hypes: one row per (sec_type, code,
min_checkin_period, hype EPISODE) — ETF + Index + Stock in one table,
``sec_type`` discriminates.

Rebuild semantics (margin_changes precedent): episodes SHIFT with new
data and non-hyped dates leave no footprint, so there is no per-date
incremental mode — every run recomputes each requested sec_type's
episodes wholesale from the FULL per-code history (the centered ±10y
percentile threshold windows need up to 2550 rows on EACH side of
every audited date anyway). --force additionally truncates the table
upfront (full-universe force only).

Usage:
  python -m builds.market_hypes                   rebuild all sec_types
  python -m builds.market_hypes --sec-type index  rebuild one sec_type
  python -m builds.market_hypes --code 159673.SZ  rebuild one security
  python -m builds.market_hypes --force           truncate + full recompute

Prerequisite: stats.{etf,index,stock}_{identity,basic_stats,tech_stats,
liquidity_margin} (+ stats.etf_adjustment) populated by the builds.
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

# Ensure project root is on sys.path so ``_common`` is importable when run
# directly via ``python -m builds.market_hypes`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    get_db_pool_async,
    print_build_header,
    print_wall_time,
    add_force_arg,
)

setup_utf8_stdout()

# cudf.pandas activation — must run before pandas first import (runner /
# compute import pandas at module scope; the package __init__ also
# activates, this covers the direct-module-import order here).
from _common.df_utils._activate import activate  # noqa: E402
activate()

from builds.market_hypes import TABLE, run_build  # noqa: E402
from builds.market_hypes.config import SEC_TYPES  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Market-hype EPISODE detector build (ETF + Index + "
                    "Stock) -> stats.mov_ave_market_hypes."
    )
    add_force_arg(ap)
    ap.add_argument(
        "--sec-type", choices=SEC_TYPES, default=None,
        help="Rebuild only this sec_type (for testing). Default: all.",
    )
    ap.add_argument(
        "--max-concurrent", type=int, default=20,
        help="Maximum parallel COPY chunks. Each chunk acquires one "
             "Postgres backend connection from the pool, so this also "
             "sets the pool's max_size. Local dev DB has "
             "max_connections=100 with ~2 in use, so 20 is safe. "
             "Reduce if you see 'too many clients' errors. Default: 20.",
    )
    ap.add_argument(
        "--code", default=None,
        help="Rebuild the episodes of this single security only "
             "(bypasses the active-universe pre-filter). Mutually "
             "exclusive with --force.",
    )
    args = ap.parse_args()

    if args.code and args.force:
        print("ERROR: --code and --force are mutually exclusive.")
        sys.exit(2)

    sec_types = (args.sec_type,) if args.sec_type else SEC_TYPES
    max_concurrent = max(1, args.max_concurrent)

    t0 = time.time()
    print_build_header(
        "BUILD MARKET HYPES (ETF + INDEX + STOCK)",
        table=TABLE,
        sec_types=", ".join(sec_types),
        mode=(
            f"SINGLE-CODE {args.code} (full recompute for this security)"
            if args.code else
            "FORCE (truncate + full recompute)" if args.force
            else "wholesale per-sec_type recompute"
        ),
    )

    conn = await get_db_connection_async()
    pool = await get_db_pool_async(min_size=1, max_size=max_concurrent)
    try:
        await run_build(
            conn,
            force=args.force,
            sec_types=sec_types,
            code_filter=args.code,
            max_concurrent=max_concurrent,
            pool=pool,
        )
    finally:
        # Close with a timeout — after heavy bulk writes the PostgreSQL
        # server can be saturated with WAL checkpoint I/O, making
        # conn.close() stall on the Terminate message + TCP teardown.
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
