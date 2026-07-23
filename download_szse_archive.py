import random
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import requests

from _download_szse_sse_commons import (
    REFERER_ARCHIVE,
    build_headers,
    run_szse_download,
)
from _download_commons import DEFAULT_START_DATE


STOCK_EXTRA_PARAMS: Dict[str, Optional[str]] = {}
ETF_EXTRA_PARAMS: Dict[str, Optional[str]] = {
    "txtHistoryMaxDate": None,
    "radioClass": "15,16,18,38,55,56,58,65,MF",
    "txtSite": "all",
}

SECURITY_CFGS: Dict[str, Dict[str, object]] = {
    "stock": {
        "catalogid": "1815_stock",
        "tabkey": "tab1",
        "prefix": "szse_stock",
        "extra": STOCK_EXTRA_PARAMS,
    },
    "etf": {
        "catalogid": "1815_stock",
        "tabkey": "tab2",
        "prefix": "szse_etf",
        "extra": ETF_EXTRA_PARAMS,
    },
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


def download_szse_archive(
    out_root: Optional[str] = None,
    end_date: Optional[str] = None,
    start_date: str = DEFAULT_START_DATE,
    security_types: Optional[List[str]] = None,
    sleep_sec: float = 0.8,
    session: Optional[requests.Session] = None,
) -> dict:
    """
    Download SZSE archive market data day by day for stocks and ETFs/funds.

    Each date is requested individually against
    https://www.szse.cn/market/trend/archive/index.html until start_date is
    reached (default: DEFAULT_START_DATE in _download_commons, currently
    2022-01-01).
    """
    return run_szse_download(
        caller_file=str(Path(__file__).resolve()),
        out_dirname="szse_archive",
        banner_label="archive",
        security_cfgs=SECURITY_CFGS,
        headers=ARCHIVE_HEADERS,
        params_builder=_build_archive_params,
        log_tag_fn=_archive_log_tag,
        out_root=out_root,
        end_date=end_date,
        start_date=start_date,
        security_types=security_types,
        sleep_sec=sleep_sec,
        session=session,
        code_suffix=".SZ",
    )


if __name__ == "__main__":
    print(download_szse_archive())
