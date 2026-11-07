"""csindex.com.cn index quote downloader package.

Public API:
  - CsindexQuoteRunner: orchestrator for the full download pipeline.
  - CSINDEX_BASE, CSINDEX_HEADERS, CSINDEX_SKIP_CODES, fetch_intraday: shared
    with the csindex intraday stream downloader.

Internal modules:
  - _config:    constants, endpoints, headers, logger, session/proxy helpers
  - _export:    daily OHLCV+amount export Excel download (CsindexQuoteExport)
  - _pe:        PE (peg) historical series fetch + cache (CsindexQuotePe)
  - _intraday:  latest-day intraday granular ticks (CsindexQuoteIntraday)
  - _history:   merge exports + PE -> history CSV, incremental csv append
                (CsindexQuoteHistory)
  - runner:     CsindexQuoteRunner orchestrator
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
from ._export import CsindexQuoteExport
from ._pe import CsindexQuotePe
from ._intraday import CsindexQuoteIntraday, fetch_intraday
from ._history import CsindexQuoteHistory
from .runner import CsindexQuoteRunner

__all__ = [
    "CsindexQuoteRunner",
    "CsindexQuoteExport",
    "CsindexQuotePe",
    "CsindexQuoteIntraday",
    "CsindexQuoteHistory",
    "fetch_intraday",
    "build_session",
    "make_proxy",
    "CSINDEX_BASE",
    "CSINDEX_HEADERS",
    "CSINDEX_SKIP_CODES",
    "UPDATE_WINDOW_DAYS",
    "SLEEP_SEC",
]
