"""Download SZSE option (tab6) trend market data day by day.

Uses CATALOGID=1815_stock_snapshot, TABKEY=tab6 from
https://www.szse.cn/market/trend/index.html. Writes
``szse_trend_option_{YYYYMMDD}.xlsx/.csv`` under ``temps/sse_trend/``.

DB-first mode: queries ``stats.options_identity`` to find missing trading
days, skipping dates already in the DB. Also skips dates with local 0-byte
CSV markers (previously fetched but no data found). Note: options_identity
has no exchange column (PK is date + contract_code), so db_exchange
is intentionally omitted.

The entry point is the :class:`SzseOptionsTrendDownload` DownloadJob — the
former module body is its ``run()`` and the ``download_szse_trend_option``
orchestration function became the method of the same name (same signature
and body); the request-config constants and the tiny param/log-tag builders
stay module-level.
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


# Pacing (sleep/jitter/timeouts/anti-bot toggles) resolves from this service's
# external_request_config JSON row via run_szse_download(service=...).
SERVICE = "downloads.options.szse.trend"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)
REFERER_TREND = URLS["referer_trend"]


TREND_ARCHIVE_DATE = "2025-07-01"

SECURITY_CFGS: Dict[str, Dict[str, str]] = {
    "option": {
        "catalogid": "1815_stock_snapshot",
        "tabkey": "tab6",
        "prefix": "szse_trend_option",
    },
}

# DB-first mode: stats.options_identity has PK (date, contract_code) with NO
# exchange column, so db_exchange is intentionally omitted. The
# check_identity query only filters by date when code/exchange are None.
DB_TABLE_BY_TYPE: Dict[str, str] = {
    "option": "stats.options_identity",
}

TREND_HEADERS = build_headers(REFERER_TREND)


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


class SzseOptionsTrendDownload(DownloadJob):
    """DownloadJob for the SZSE option trend fetch cycle — the former module
    body is ``run()`` and the ``download_szse_trend_option`` function is the
    method of the same name (same signature and body verbatim); pacing and
    date range stay per-call arguments. The module had no argparse."""

    description = "Download SZSE option (tab6) trend market data day by day."

    def download_szse_trend_option(
        self,
        out_root: Optional[str] = None,
        end_date: Optional[str] = None,
        start_date: str = "2025-07-01",
        sleep_sec: Optional[float] = None,
        session: Optional[requests.Session] = None,
    ) -> dict:
        """Download SZSE option (tab6) trend data; pacing resolves from the
        ``downloads.options.szse.trend`` external_request_config JSON row
        (an explicit *sleep_sec* overrides the row's base sleep only)."""
        return run_szse_download(
            caller_file=str(Path(__file__).resolve()),
            out_dirname="szse_trend",
            banner_label="trend-option",
            security_cfgs=SECURITY_CFGS,
            headers=TREND_HEADERS,
            params_builder=_build_trend_params,
            log_tag_fn=_trend_log_tag,
            out_root=out_root,
            end_date=end_date,
            start_date=start_date,
            security_types=["option"],
            sleep_sec=sleep_sec,
            session=session,
            service=SERVICE,
            exchange="SZ",
            db_table_by_type=DB_TABLE_BY_TYPE,
            skip_empty_markers=True,
        )

    def run(self) -> None:
        logger.info(self.download_szse_trend_option())


if __name__ == "__main__":
    SzseOptionsTrendDownload().execute()
