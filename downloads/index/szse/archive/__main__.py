"""Download SZSE index (tab7) archive market data day by day.

Uses CATALOGID=1815_stock, TABKEY=tab7 from
https://www.szse.cn/market/trend/archive/index.html. Writes
``szse_index_{YYYYMMDD}.xlsx/.csv`` under ``temps/sse_archive/``.

The xlsx contains ~180 indexes per day; the CSV is filtered to only
399001 深证成指, 399006 创业板指, and 399237 运输指数. The xlsx is kept in full.
No identity table — falls back to filesystem scan.
"""
from __future__ import annotations

import random
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import requests

from downloads._common.core import DEFAULT_START_DATE
from downloads._common.szse_runner import (
    REFERER_ARCHIVE,
    build_headers,
    run_szse_download,
)


INDEX_EXTRA_PARAMS: Dict[str, Optional[str]] = {}

SECURITY_CFGS: Dict[str, Dict[str, object]] = {
    "index": {
        "catalogid": "1815_stock",
        "tabkey": "tab7",
        "prefix": "szse_index",
        "extra": INDEX_EXTRA_PARAMS,
    },
}

INDEX_CODES_TO_KEEP: List[str] = ["399001", "399006", "399237"]
CODE_FILTER_BY_TYPE: Dict[str, List[str]] = {
    "index": INDEX_CODES_TO_KEEP,
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


def download_szse_archive_index(
    out_root: Optional[str] = None,
    end_date: Optional[str] = None,
    start_date: str = DEFAULT_START_DATE,
    sleep_sec: float = 5.0,
    session: Optional[requests.Session] = None,
) -> dict:
    return run_szse_download(
        caller_file=str(Path(__file__).resolve()),
        out_dirname="szse_archive",
        banner_label="archive-index",
        security_cfgs=SECURITY_CFGS,
        headers=ARCHIVE_HEADERS,
        params_builder=_build_archive_params,
        log_tag_fn=_archive_log_tag,
        out_root=out_root,
        end_date=end_date,
        start_date=start_date,
        security_types=["index"],
        sleep_sec=sleep_sec,
        session=session,
        code_suffix=".SZ",
        code_filter_by_type=CODE_FILTER_BY_TYPE,
    )


if __name__ == "__main__":
    print(download_szse_archive_index())
