"""csindex.com.cn index quote downloader package.

Public API:
  - download_index: orchestrator for the full download pipeline.
  - CSINDEX_BASE, CSINDEX_HEADERS, CSINDEX_SKIP_CODES, fetch_intraday: shared
    with the csindex intraday stream downloader.

Internal modules:
  - _config:    constants, endpoints, headers, logger, session/proxy helpers
  - _export:    daily OHLCV+amount export Excel download
  - _pe:        PE (peg) historical series fetch + cache
  - _intraday:  latest-day intraday granular ticks
  - _history:   merge exports + PE -> history CSV, incremental csv append
  - runner:     download_index orchestrator
"""
from __future__ import annotations

from ._config import (
    CSINDEX_BASE,
    CSINDEX_HEADERS,
    CSINDEX_SKIP_CODES,
    UPDATE_WINDOW_DAYS,
    SLEEP_SEC,
    build_session,
    make_proxy,
)
from ._export import download_export_excel
from ._pe import fetch_pe_series, load_pe_cache, save_pe_cache
from ._intraday import fetch_intraday, save_intraday
from ._history import build_history_csv, append_missing_dates_to_csv
from .runner import download_index

__all__ = [
    "download_index",
    "download_export_excel",
    "fetch_pe_series",
    "load_pe_cache",
    "save_pe_cache",
    "fetch_intraday",
    "save_intraday",
    "build_history_csv",
    "append_missing_dates_to_csv",
    "build_session",
    "make_proxy",
    "CSINDEX_BASE",
    "CSINDEX_HEADERS",
    "CSINDEX_SKIP_CODES",
    "UPDATE_WINDOW_DAYS",
    "SLEEP_SEC",
]
