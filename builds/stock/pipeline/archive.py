"""SSE + BSE per-stock archive ({code}_trend.csv) and SSE PE loading.

The two archives share one CSV contract — the unified trend schema with
canonical codes, written by ``downloads.stock.sse.archive`` and
``downloads.stock.bse.archive`` — so both loaders delegate to
``_read_archive_trend_files`` with only directory + exchange differing.
SSE additionally has PE snapshot files ({code}_pe.csv).
"""
from __future__ import annotations

import pandas as pd

from builds._commons.paths import BSE_ARCHIVE_DIR, SSE_PE_DIR
from builds.stock._helpers import (
    _read_archive_trend_files,
    _read_sse_pe_files,
)

import logging
logger = logging.getLogger(__name__)


async def _load_archive_ohlcv(
    label: str,
    archive_dir: str,
    exchange: str,
    conn,
    start_date: str | None,
    end_date: str | None,
    limit: int | None,
    force: bool,
    code_filter: str | None,
) -> pd.DataFrame:
    """Load one exchange's per-stock archive OHLCV (incremental: file mtime
    + per-code DB max).

    The reader filters rows per code to dates beyond the DB max, so no
    anti-join against stock_identity is needed here.
    """
    logger.info(f"\n    Loading {label} archive historical OHLCV from {archive_dir} …")
    archive_df = await _read_archive_trend_files(
        archive_dir, exchange, start_date, end_date, limit=limit,
        conn=conn, force=force, verbose=True, code_filter=code_filter,
    )
    if len(archive_df) == 0:
        logger.info(f"    [ARCHIVE] No {label} archive trend files found")
        return archive_df

    n_archive_total = len(archive_df)
    n_archive_stocks = archive_df["code"].nunique()
    # Scalar Timestamp.strftime has no cudf fast path — format on
    # GPU once, then string min/max (== chronological for YYYY-MM-DD).
    archive_date_strs = archive_df["date"].dt.strftime("%Y-%m-%d")
    d0 = archive_date_strs.min()
    d1 = archive_date_strs.max()
    logger.info(f"    [ARCHIVE] {n_archive_total:,} rows | {n_archive_stocks} stocks | "
          f"{d0} → {d1}")
    return archive_df


async def load_sse_archive(
    conn,
    start_date: str | None,
    end_date: str | None,
    limit: int | None,
    force: bool,
    code_filter: str | None,
) -> pd.DataFrame:
    """Load the SSE archive (temps/sse_archive/{code}_trend.csv)."""
    return await _load_archive_ohlcv(
        "SSE", SSE_PE_DIR, "SS",
        conn, start_date, end_date, limit, force, code_filter,
    )


async def load_bse_archive(
    conn,
    start_date: str | None,
    end_date: str | None,
    limit: int | None,
    force: bool,
    code_filter: str | None,
) -> pd.DataFrame:
    """Load the BSE archive (temps/bse_archive/{code}_trend.csv, full
    per-stock kline history from downloads.stock.bse.archive).

    BSE has no PE archive files — the 市盈率 column stays empty and PE for
    current dates flows through the daily bse_trend snapshot path instead.
    """
    return await _load_archive_ohlcv(
        "BSE", BSE_ARCHIVE_DIR, "BJ",
        conn, start_date, end_date, limit, force, code_filter,
    )


async def load_sse_pe(
    conn,
    force: bool,
    code_filter: str | None,
) -> pd.DataFrame:
    """Load SSE PE snapshots ({code}_pe.csv, incremental by file mtime)."""
    logger.info(f"\n    Merging SSE PE snapshots from {SSE_PE_DIR} …")
    return await _read_sse_pe_files(
        SSE_PE_DIR, conn=conn, force=force, verbose=True,
        code_filter=code_filter,
    )
