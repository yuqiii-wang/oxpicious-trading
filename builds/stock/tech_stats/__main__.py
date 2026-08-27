"""Entry point for builds.stock.tech_stats (standalone mode).

Run via ``python -m builds.stock.tech_stats``.

For integrated mode (called from builds.stock), import run_tech_stats_chunked:
    from builds.stock.tech_stats import run_tech_stats_chunked
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()
import argparse
import asyncio
import time

from _common.build_commons import (
    setup_utf8_stdout,
    get_db_or_exit,
    print_build_header,
    print_wall_time,
    add_force_arg,
)
from builds.stock.tech_stats import run_tech_stats_chunked, TABLE, SOURCE_TABLE

setup_utf8_stdout()


async def main():
    ap = argparse.ArgumentParser(
        description="Build stats.stock_tech_stats from stock_basic_stats.close."
    )
    ap.add_argument(
        "--chunk-size", type=int, default=500,
        help="Number of codes per chunk (default 500).",
    )
    add_force_arg(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "BUILD STOCK TECH_STATS  ·  MA5/20/60/120/255 + EMA6/10/20/60/120/255 from close",
        table=TABLE,
        source=SOURCE_TABLE,
        mode="FORCE (full recompute)" if args.force else "incremental (missing pairs only)",
    )

    conn = await get_db_or_exit()
    try:
        total = await run_tech_stats_chunked(
            conn, force=args.force, chunk_size=args.chunk_size, verbose=True
        )
        print(f"\n[DONE] Total rows upserted into {TABLE}: {total:,}", flush=True)
    finally:
        try:
            await conn.close()
        except Exception:
            pass

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
