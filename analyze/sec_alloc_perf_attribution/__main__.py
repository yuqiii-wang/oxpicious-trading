"""Entry point for ``python -m analyze.sec_alloc_perf_attribution``.

Thin CLI wrapper around ``run_perf_attribution`` (in ``run.py``). The
pipeline logic lives in ``run.py`` so it can ALSO be called as an internal
step of ``analyze.industry_sentiments`` (which passes its own connection
down). Running this module standalone opens its own connection, runs the
pipeline, and closes it.

See ``run.py`` for the pipeline docstring.
"""
from __future__ import annotations

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
from analyze.sec_alloc_perf_attribution.run import run_perf_attribution  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Sec alloc perf attribution analysis (Index x Index)."
    )
    add_force_arg(ap)
    args = ap.parse_args()

    t0 = time.time()
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
    asyncio.run(main())
