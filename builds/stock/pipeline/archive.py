"""SSE archive ({code}_trend.csv) and PE ({code}_pe.csv) loading."""
from __future__ import annotations

import pandas as pd

from builds._commons.paths import SSE_PE_DIR
from builds.stock._helpers import (
    _read_sse_archive_trend_files,
    _read_sse_pe_files,
)


async def load_sse_archive(
    conn,
    start_date: str | None,
    end_date: str | None,
    limit: int | None,
    force: bool,
    code_filter: str | None,
) -> pd.DataFrame:
    """Load SSE archive OHLCV (incremental: file mtime + per-code DB max).

    The reader filters rows per code to dates beyond the DB max, so no
    anti-join against stock_identity is needed here.
    """
    print(f"\n    Loading SSE archive historical OHLCV from {SSE_PE_DIR} …", flush=True)
    archive_df = await _read_sse_archive_trend_files(
        SSE_PE_DIR, start_date, end_date, limit=limit,
        conn=conn, force=force, verbose=True, code_filter=code_filter,
    )
    if len(archive_df) == 0:
        print("    [ARCHIVE] No SSE archive trend files found", flush=True)
        return archive_df

    n_archive_total = len(archive_df)
    n_archive_stocks = archive_df["code"].nunique()
    # Scalar Timestamp.strftime has no cudf fast path — format on
    # GPU once, then string min/max (== chronological for YYYY-MM-DD).
    archive_date_strs = archive_df["date"].dt.strftime("%Y-%m-%d")
    d0 = archive_date_strs.min()
    d1 = archive_date_strs.max()
    print(f"    [ARCHIVE] {n_archive_total:,} rows | {n_archive_stocks} stocks | "
          f"{d0} → {d1}", flush=True)
    return archive_df


async def load_sse_pe(
    conn,
    force: bool,
    code_filter: str | None,
) -> pd.DataFrame:
    """Load SSE PE snapshots ({code}_pe.csv, incremental by file mtime)."""
    print(f"\n    Merging SSE PE snapshots from {SSE_PE_DIR} …", flush=True)
    return await _read_sse_pe_files(
        SSE_PE_DIR, conn=conn, force=force, verbose=True,
        code_filter=code_filter,
    )
