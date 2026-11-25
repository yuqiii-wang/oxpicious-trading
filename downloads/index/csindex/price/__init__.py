"""csindex.com.cn index PRICE downloader package (the daily close-quote
sweep; the weekly PE fetch is the sibling ``pe`` service).

Service split (2026-11-23): the former ``downloads.index.csindex.quote``
package — price and PE in one service — split so each audits, paces and
gates under its own name. THIS package is the daily price service (the
1m-sweep + intraday entry AND the shared fetch machinery the pe service
imports); the PE fetch lives in ``downloads.index.csindex.pe``.

Public API:
  - CsindexQuoteRunner: orchestrator for the full download pipeline
    (also the pe service's engine via its ``pe_only`` path).
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
