"""SZSE ETF/fund (tab2) archive market data download — day by day.

Uses CATALOGID=1815_stock, TABKEY=tab2 from
https://www.szse.cn/market/trend/archive/index.html. Writes
``szse_etf_{YYYYMMDD}.xlsx/.csv`` under ``temps/sse_archive/``.

DB-first mode: queries ``stats.etf_identity`` (code_suffix='SZ').
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


ETF_EXTRA_PARAMS: Dict[str, Optional[str]] = {
    "txtHistoryMaxDate": None,
    "radioClass": "15,16,18,38,55,56,58,65,MF",
    "txtSite": "all",
}

SECURITY_CFGS: Dict[str, Dict[str, object]] = {
    "etf": {
        "catalogid": "1815_stock",
        "tabkey": "tab2",
        "prefix": "szse_etf",
        "extra": ETF_EXTRA_PARAMS,
    },
}

DB_TABLE_BY_TYPE: Dict[str, str] = {
    "etf": "stats.etf_identity",
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


def download_szse_archive_etf(
    out_root: Optional[str] = None,
    end_date: Optional[str] = None,
    start_date: str = DEFAULT_START_DATE,
    sleep_sec: float = 5.0,
    session: Optional[requests.Session] = None,
) -> dict:
    return run_szse_download(
        caller_file=str(Path(__file__).resolve()),
        out_dirname="szse_archive",
        banner_label="archive-etf",
        security_cfgs=SECURITY_CFGS,
        headers=ARCHIVE_HEADERS,
        params_builder=_build_archive_params,
        log_tag_fn=_archive_log_tag,
        out_root=out_root,
        end_date=end_date,
        start_date=start_date,
        security_types=["etf"],
        sleep_sec=sleep_sec,
        session=session,
        code_suffix=".SZ",
        db_table_by_type=DB_TABLE_BY_TYPE,
        db_code_suffix="SZ",
    )
