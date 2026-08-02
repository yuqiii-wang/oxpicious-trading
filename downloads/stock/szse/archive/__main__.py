"""Download SZSE stock (tab1) archive market data day by day.

Uses CATALOGID=1815_stock, TABKEY=tab1 from
https://www.szse.cn/market/trend/archive/index.html. Writes
``szse_stock_{YYYYMMDD}.xlsx/.csv`` under ``temps/sse_archive/``.

DB-first mode: queries ``stats.stock_identity`` (code_suffix='SZ').
"""
from __future__ import annotations

import random
from datetime import date
from pathlib import Path
from typing import Dict, Optional

import requests

from downloads._common.core import DEFAULT_START_DATE
from downloads._common.szse_runner import (
    REFERER_ARCHIVE,
    build_headers,
    run_szse_download,
)


STOCK_EXTRA_PARAMS: Dict[str, Optional[str]] = {}

SECURITY_CFGS: Dict[str, Dict[str, object]] = {
    "stock": {
        "catalogid": "1815_stock",
        "tabkey": "tab1",
        "prefix": "szse_stock",
        "extra": STOCK_EXTRA_PARAMS,
    },
}

DB_TABLE_BY_TYPE: Dict[str, str] = {
    "stock": "stats.stock_identity",
}

ARCHIVE_HEADERS = build_headers(REFERER_ARCHIVE)


def _build_archive_params(security_type: str, trade_date: date) -> Dict[str, object]:
    cfg = SECURITY_CFGS[security_type]
    date_str = trade_date.strftime("%Y-%m-%d")
    params: Dict[str, object] = {
        "SHOWTYPE": "xlsx",
        "CATALOGID": cfg["catalogid"],
        "TABKEY": cfg["tabkey"],
        "txtBeginDate": date_str,
        "random": random.random(),
    }
    extra = cfg["extra"]
    if extra:
        for k, v in extra.items():
            params[k] = date_str if v is None else v
    return params


def _archive_log_tag(security_type: str, ymd: str) -> str:
    return f"[{security_type} {ymd}]"


def download_szse_archive_stock(
    out_root: Optional[str] = None,
    end_date: Optional[str] = None,
    start_date: str = DEFAULT_START_DATE,
    sleep_sec: float = 5.0,
    session: Optional[requests.Session] = None,
) -> dict:
    return run_szse_download(
        caller_file=str(Path(__file__).resolve()),
        out_dirname="szse_archive",
        banner_label="archive-stock",
        security_cfgs=SECURITY_CFGS,
        headers=ARCHIVE_HEADERS,
        params_builder=_build_archive_params,
        log_tag_fn=_archive_log_tag,
        out_root=out_root,
        end_date=end_date,
        start_date=start_date,
        security_types=["stock"],
        sleep_sec=sleep_sec,
        session=session,
        code_suffix=".SZ",
        db_table_by_type=DB_TABLE_BY_TYPE,
        db_code_suffix="SZ",
    )


if __name__ == "__main__":
    print(download_szse_archive_stock())
