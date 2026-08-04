"""Download SZSE ETF/fund (tab2) trend market data day by day.

Uses CATALOGID=1815_stock_snapshot, TABKEY=tab2 from
https://www.szse.cn/market/trend/index.html. Writes
``szse_trend_etf_{YYYYMMDD}.xlsx/.csv`` under ``temps/sse_trend/``.

DB-first mode: queries ``stats.etf_identity`` (code_suffix='SZ') to find
missing trading days, skipping dates already in the DB. Also skips dates
with local 0-byte CSV markers (previously fetched but no data found).
"""
from __future__ import annotations

import random
from datetime import date
from pathlib import Path
from typing import Dict, Optional

import requests

from downloads._common.szse_runner import (
    REFERER_TREND,
    build_headers,
    run_szse_download,
)


TREND_ARCHIVE_DATE = "2025-07-01"

SECURITY_CFGS: Dict[str, Dict[str, str]] = {
    "etf": {
        "catalogid": "1815_stock_snapshot",
        "tabkey": "tab2",
        "prefix": "szse_trend_etf",
    },
}

DB_TABLE_BY_TYPE: Dict[str, str] = {
    "etf": "stats.etf_identity",
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


def download_szse_trend_etf(
    out_root: Optional[str] = None,
    end_date: Optional[str] = None,
    start_date: str = "2025-07-01",
    sleep_sec: float = 5.0,
    session: Optional[requests.Session] = None,
) -> dict:
    return run_szse_download(
        caller_file=str(Path(__file__).resolve()),
        out_dirname="szse_trend",
        banner_label="trend-etf",
        security_cfgs=SECURITY_CFGS,
        headers=TREND_HEADERS,
        params_builder=_build_trend_params,
        log_tag_fn=_trend_log_tag,
        out_root=out_root,
        end_date=end_date,
        start_date=start_date,
        security_types=["etf"],
        sleep_sec=sleep_sec,
        session=session,
        code_suffix=".SZ",
        db_table_by_type=DB_TABLE_BY_TYPE,
        db_code_suffix="SZ",
        skip_empty_markers=True,
    )


if __name__ == "__main__":
    print(download_szse_trend_etf())
