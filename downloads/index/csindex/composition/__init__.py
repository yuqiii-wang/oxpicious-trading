"""Public API for csindex.com.cn index composition downloader."""
from __future__ import annotations

from ._cache import csv_filename_for, find_cached_csv
from ._config import (
    CLOSEWEIGHT_URL_TEMPLATE,
    COLUMN_MATCHERS,
    CSINDEX_SKIP_CODES,
    DEBT_SECTOR_ID,
    DEBT_SECTOR_INDUSTRY_IDS,
    SLEEP_SEC,
    logger,
)
from ._fetch import fetch_closeweight_xls
from ._parse import (
    _extract_snapshot_date,
    _normalize_columns,
    _normalize_stock_code,
    normalize_closeweight_df,
    parse_closeweight_xls,
)
from .runner import download_index_composition

__all__ = [
    # Config
    "CLOSEWEIGHT_URL_TEMPLATE",
    "COLUMN_MATCHERS",
    "CSINDEX_SKIP_CODES",
    "DEBT_SECTOR_ID",
    "DEBT_SECTOR_INDUSTRY_IDS",
    "SLEEP_SEC",
    "logger",
    # Cache
    "csv_filename_for",
    "find_cached_csv",
    # Fetch
    "fetch_closeweight_xls",
    # Parse
    "parse_closeweight_xls",
    "normalize_closeweight_df",
    # Runner
    "download_index_composition",
]
