"""Entry point for ``python -m analyze.sec_alloc_perf_attribution``.

Thin CLI wrapper around ``run_perf_attribution`` (in ``run.py``). The
pipeline logic lives in ``run.py`` so it can ALSO be called as an internal
step of ``analyze.industry_sentiments`` (which passes its own connection
down). Running this module standalone opens its own connection, runs the
pipeline, and closes it.

See ``run.py`` for the pipeline docstring.
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
# directly via ``python -m analyze.sec_alloc_perf_attribution`` or as a script.
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

from analyze.sec_alloc_perf_attribution.config import (  # noqa: E402
    TABLE,
    TOP_N_NON_BROAD,
)
from analyze.sec_alloc_perf_attribution.run import (  # noqa: E402
    run_corr_update,
    run_etf_backfill,
    run_perf_attribution,
)


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Sec alloc perf attribution analysis (Index x Index)."
    )
    add_force_arg(ap)
    ap.add_argument(
        "--corr", action="store_true",
        help="Corr-only build: recompute corr_20d/60d/255d on stride-grid "
             "dates and upsert them onto existing rows (the main run "
             "writes rows with corr OFF by default).",
    )
    ap.add_argument(
        "--etf", action="store_true",
        help="ETF-only backfill: attach benchmark_etf_trading_amount / "
             "code_etf_trading_amount / ratio (+ MA5) from "
             "stats.index_exts onto EXISTING rows in-place (year-chunked "
             "UPDATE). For rows written before builds.index's exts phase "
             "populated index_exts — avoids a full --force recompute.",
    )
    args = ap.parse_args()

    t0 = time.time()

    if args.etf:
        print_build_header(
            "ANALYZE SEC ALLOC PERF ATTRIBUTION — ETF-ONLY BACKFILL",
            table=TABLE,
            sec_types="index",
            top_n_non_broad=f"{TOP_N_NON_BROAD}",
            mode="etf-only (in-place attach from stats.index_exts)",
        )
        conn = await get_db_connection_async()
        try:
            await run_etf_backfill(conn)
        finally:
            try:
                await asyncio.wait_for(conn.close(), timeout=10)
            except (asyncio.TimeoutError, Exception):
                pass
        print_wall_time(t0)
        return

    if args.corr:
        print_build_header(
            "ANALYZE SEC ALLOC PERF ATTRIBUTION — CORR BUILD (grid dates)",
            table=TABLE,
            sec_types="index",
            top_n_non_broad=f"{TOP_N_NON_BROAD}",
            mode="corr-only (upsert corr_20d/60d/255d on grid dates)",
        )
        conn = await get_db_connection_async()
        try:
            await run_corr_update(conn)
        finally:
            try:
                await asyncio.wait_for(conn.close(), timeout=10)
            except (asyncio.TimeoutError, Exception):
                pass
        print_wall_time(t0)
        return

    print_build_header(
        "ANALYZE SEC ALLOC PERF ATTRIBUTION (INDEX x INDEX)",
        table=TABLE,
        sec_types="index",
        top_n_non_broad=f"{TOP_N_NON_BROAD}",
        mode="FORCE (full recompute)" if args.force
             else "incremental (missing dates only)",
    )

    conn = await get_db_connection_async()
    try:
        await run_perf_attribution(conn, force=args.force)
    finally:
        # Close with a timeout — after heavy bulk inserts the PostgreSQL
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
