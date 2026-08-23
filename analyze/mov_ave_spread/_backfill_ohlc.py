"""One-off backfill driver: run ONLY the OHLC internal step (run_ohlc)
per sec_type, without recomputing detail / RSI / holiday / EMA / trading-amt.

Full-recompute mode for the anchor-semantics change (top anchors = close of
the highest/lowest close date more than 20% of the window before `date`;
2nd anchors = intraday high/low of the best separated local peak/trough):
deletes ALL rows of each sec_type from
analysis.mov_ave_spreads_detail_ohlc first so incremental mode sees every
source date as missing and recomputes the whole universe (values + DATE
columns) with the new semantics.
Run via ``python -m analyze.mov_ave_spread._backfill_ohlc``.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

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
)

setup_utf8_stdout()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate  # noqa: E402
activate()

from analyze.mov_ave_spread.fetch import fetch_source_data  # noqa: E402
from analyze.mov_ave_spread.ohlc import run_ohlc  # noqa: E402
from analyze.mov_ave_spread.config import OHLC_TABLE  # noqa: E402


async def main() -> None:
    t0 = time.time()
    max_concurrent = 20
    conn = await get_db_connection_async()
    pool = await get_db_pool_async(min_size=1, max_size=max_concurrent)
    try:
        for st in ("etf", "index"):
            # Close-based high/low semantic change: wipe the sec_type's rows
            # so incremental mode recomputes EVERY date (values + dates).
            status: str = await conn.execute(
                f"DELETE FROM {OHLC_TABLE} WHERE sec_type = $1", st
            )
            n_deleted: int = (
                int(status.rsplit(" ", 1)[-1]) if status else 0
            )
            print(f"\n[{st}] deleted {n_deleted:,} existing rows "
                  f"(full recompute)", flush=True)
            print(f"[{st}] fetching FULL per-code source history...", flush=True)
            df = await fetch_source_data(conn, st, target_dates=None)
            print(f"[{st}]   {len(df):,} source rows", flush=True)
            if df.empty:
                print(f"[{st}]   no source data; skipping.", flush=True)
                continue
            await run_ohlc(
                conn, df, force=False, pool=pool,
                max_concurrent=max_concurrent, sec_type=st,
            )
            del df
        print(f"\nBackfill wall time: {time.time() - t0:.1f}s", flush=True)
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass
        try:
            await asyncio.wait_for(pool.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pool.terminate()


if __name__ == "__main__":
    asyncio.run(main())
