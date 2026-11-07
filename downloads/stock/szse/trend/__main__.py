"""Download SZSE stock (tab1) trend market data day by day.

Uses CATALOGID=1815_stock_snapshot, TABKEY=tab1 from
https://www.szse.cn/market/trend/index.html. Writes
``szse_trend_stock_{YYYYMMDD}.xlsx/.csv`` under ``temps/sse_trend/``.

DB-first mode: queries ``stats.stock_identity`` (exchange='SZ') to find
missing trading days, skipping dates already in the DB. Also skips dates
with local 0-byte CSV markers (previously fetched but no data found).

The entry point is the :class:`SzseStockTrendDownload` DownloadJob — the
former ``download_szse_trend_stock`` body is its method (verbatim) and
the param-builder helpers stay module-level.
"""
from __future__ import annotations


import random
from datetime import date
from pathlib import Path
from typing import Dict, Optional

import requests

from downloads._common import DownloadJob, service_urls
from downloads._common.exchanges.szse import (
    build_headers,
    run_szse_download,
)

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("trend")

SERVICE = "downloads.stock.szse.trend"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)


TREND_ARCHIVE_DATE = "2025-07-01"

SECURITY_CFGS: Dict[str, Dict[str, str]] = {
    "stock": {
        "catalogid": "1815_stock_snapshot",
        "tabkey": "tab1",
        "prefix": "szse_trend_stock",
    },
}

DB_TABLE_BY_TYPE: Dict[str, str] = {
    "stock": "stats.stock_identity",
}

TREND_HEADERS = build_headers(URLS["referer_trend"])


def _build_trend_params(security_type: str, trade_date: date) -> Dict[str, object]:
    cfg = SECURITY_CFGS[security_type]
    date_str = trade_date.strftime("%Y-%m-%d")
    return {
        "SHOWTYPE": "xlsx",
        "CATALOGID": cfg["catalogid"],
        "TABKEY": cfg["tabkey"],
        "txtBeginDate": date_str,
        "txtEndDate": date_str,
        "archiveDate": TREND_ARCHIVE_DATE,
        "random": random.random(),
    }


def _trend_log_tag(security_type: str, ymd: str) -> str:
    return f"[trend-{security_type} {ymd}]"


class SzseStockTrendDownload(DownloadJob):
    """DownloadJob for the SZSE stock (tab1) trend fetch cycle — a thin
    delegate to the shared ``run_szse_download`` helper (exchanges.szse).
    The former module-level ``download_szse_trend_stock`` became a method
    and run() is the former __main__ body verbatim; the param-builder and
    log-tag helpers above stay module-level."""

    def download_szse_trend_stock(
        self,
        out_root: Optional[str] = None,
        end_date: Optional[str] = None,
        start_date: str = "2025-07-01",
        session: Optional[requests.Session] = None,
    ) -> dict:
        """Former module-level orchestrator — moved verbatim into the job
        class; ``run()`` calls it and logs the returned summary dict."""
        return run_szse_download(
            caller_file=str(Path(__file__).resolve()),
            out_dirname="szse_trend",
            banner_label="trend-stock",
            security_cfgs=SECURITY_CFGS,
            headers=TREND_HEADERS,
            params_builder=_build_trend_params,
            log_tag_fn=_trend_log_tag,
            out_root=out_root,
            end_date=end_date,
            start_date=start_date,
            security_types=["stock"],
            session=session,
            service=SERVICE,
            exchange="SZ",
            db_table_by_type=DB_TABLE_BY_TYPE,
            db_exchange="SZ",
            skip_empty_markers=True,
        )

    def run(self) -> None:
        """One fetch cycle (the former __main__ body verbatim)."""
        logger.info(self.download_szse_trend_stock())


if __name__ == "__main__":
    SzseStockTrendDownload().execute()
