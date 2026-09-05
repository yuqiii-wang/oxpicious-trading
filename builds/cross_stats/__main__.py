"""Entry point for ``python -m builds.cross_stats``.

Thin CLI wrapper around ``run_cross_stats`` / ``run_corr_update``
(runner.py). The pipeline logic lives in runner.py so it can ALSO be
called as an internal step of a downstream build (which passes its own
connection down). Running this module standalone opens its own
connection, runs the pipeline, and closes it.

Populates stats.cross_stats (+ stats.cross_stats_dates):
  • PAIR grain (sec_type='index')  — pandas/cudf pipeline, chunked COPY.
  • INDUSTRY grain (sec_type='industry') — single INSERT...SELECT.

Usage:
  python -m builds.cross_stats            incremental (missing dates only)
  python -m builds.cross_stats --force    truncate + full recompute
  python -m builds.cross_stats --corr     corr-only upsert on stride-20
                                          grid dates (base cols untouched)

Prerequisite (preflight gate exits(1) otherwise): stats.sec_composition
index holdings — run ``python -m builds.index`` first.
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
# directly via ``python -m builds.cross_stats`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    print_build_header,
    print_wall_time,
    add_force_arg,
)

setup_utf8_stdout()

# cudf.pandas activation — must run before pandas first import (runner.py
# imports pandas at module scope).
from _common.df_utils._activate import activate  # noqa: E402
activate()

from builds.cross_stats.config import (  # noqa: E402
    TABLE,
    TOP_N_NON_BROAD,
)
from builds.cross_stats.runner import (  # noqa: E402
    run_corr_update,
    run_cross_stats,
)


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Cross-security stats build (pair + industry grain) "
                    "→ stats.cross_stats."
    )
    add_force_arg(ap)
    ap.add_argument(
        "--corr", action="store_true",
        help="Corr-only build: recompute corr_20d/60d/255d on stride-20 "
             "grid dates and upsert them onto existing rows (the main run "
             "writes rows with corr OFF by default).",
    )
    args = ap.parse_args()

    t0 = time.time()

    if args.corr:
        print_build_header(
            "BUILD CROSS STATS — CORR BUILD (stride-20 grid dates)",
            table=TABLE,
            sec_types="index",
            top_n_non_broad=f"{TOP_N_NON_BROAD}",
            mode="corr-only (upsert corr_20d/60d/255d on grid dates)",
        )
        conn = await get_db_connection_async()
        try:
            await run_corr_update(conn)
        finally:
            # Close with a timeout — after heavy bulk writes the PostgreSQL
            # server can be saturated with WAL checkpoint I/O, making
            # conn.close() stall on the Terminate message + TCP teardown.
            try:
                await asyncio.wait_for(conn.close(), timeout=10)
            except (asyncio.TimeoutError, Exception):
                pass
        print_wall_time(t0)
        return

    print_build_header(
        "BUILD CROSS STATS (PAIR + INDUSTRY GRAIN)",
        table=TABLE,
        sec_types="index, industry",
        top_n_non_broad=f"{TOP_N_NON_BROAD}",
        mode="FORCE (full recompute)" if args.force
             else "incremental (missing dates only)",
    )

    conn = await get_db_connection_async()
    try:
        await run_cross_stats(conn, force=args.force)
    finally:
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
