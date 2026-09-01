"""Entry point for builds.stock.tech_stats (standalone mode).

Run via ``python -m builds.stock.tech_stats`` (add ``--date YYYY-MM-DD``
to force a single-date refresh; add ``--force`` for a full recompute).

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
from datetime import date

from _common.build_commons import (
    setup_utf8_stdout,
    get_db_or_exit,
    print_build_header,
    print_wall_time,
    add_force_arg,
    add_date_arg,
    parse_date_arg,
    enforce_date_force_exclusion,
    forced_date_scope,
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
    add_date_arg(ap)
    args = ap.parse_args()
    enforce_date_force_exclusion(args)
    forced = parse_date_arg(args.date)

    t0 = time.time()
    if forced is not None:
        mode = f"DATE MODE (single-date refresh: {forced})"
    elif args.force:
        mode = "FORCE (full recompute)"
    else:
        mode = "incremental (missing pairs only)"
    print_build_header(
        "BUILD STOCK TECH_STATS  ·  MA5/20/60/120/255 + EMA6/10/20/60/120/255 from close",
        table=TABLE,
        source=SOURCE_TABLE,
        mode=mode,
    )

    conn = await get_db_or_exit()
    try:
        target_dates: set[date] | None = None
        if forced is not None:
            # Uniform --date semantics: exit(1) when the forced date has
            # no rows in the source table; otherwise the runner's
            # target_dates mechanism bypasses the max-date skip and
            # recomputes ONLY this date (upsert refresh, no truncation).
            rows = await conn.fetch(f"SELECT DISTINCT date FROM {SOURCE_TABLE}")
            target_dates = forced_date_scope(
                {r["date"] for r in rows}, forced,
                source_label=str(SOURCE_TABLE),
            )
        total = await run_tech_stats_chunked(
            conn, force=args.force, chunk_size=args.chunk_size, verbose=True,
            target_dates=target_dates,
        )
        print(f"\n[DONE] Total rows upserted into {TABLE}: {total:,}", flush=True)
    finally:
        try:
            await conn.close()
        except Exception:
            pass

    print_wall_time(t0)


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
