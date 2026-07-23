import random
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import requests

from _download_szse_sse_commons import (
    REFERER_TREND,
    build_headers,
    run_szse_download,
)


TREND_ARCHIVE_DATE = "2025-07-01"

SECURITY_CFGS: Dict[str, Dict[str, str]] = {
    "stock": {
        "catalogid": "1815_stock_snapshot",
        "tabkey": "tab1",
        "prefix": "szse_trend_stock",
    },
    "etf": {
        "catalogid": "1815_stock_snapshot",
        "tabkey": "tab2",
        "prefix": "szse_trend_etf",
    },
    "option": {
        "catalogid": "1815_stock_snapshot",
        "tabkey": "tab6",
        "prefix": "szse_trend_option",
    },
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


def download_szse_trend(
    out_root: Optional[str] = None,
    end_date: Optional[str] = None,
    start_date: str = "2025-07-01",
    security_types: Optional[List[str]] = None,
    sleep_sec: float = 0.8,
    session: Optional[requests.Session] = None,
) -> dict:
    """
    Download SZSE trend page market data day by day for stocks, ETFs/funds,
    and options.

    Uses CATALOGID=1815_stock_snapshot from
    https://www.szse.cn/market/trend/index.html with ``txtBeginDate`` and
    ``txtEndDate`` set to the **same** business date each request, walking
    backwards from *end_date* until *start_date* (default 2025-07-01).

    ``security_types`` defaults to ``["stock", "etf", "option"]``.
    """
    return run_szse_download(
        caller_file=str(Path(__file__).resolve()),
        out_dirname="szse_trend",
        banner_label="trend",
        security_cfgs=SECURITY_CFGS,
        headers=TREND_HEADERS,
        params_builder=_build_trend_params,
        log_tag_fn=_trend_log_tag,
        out_root=out_root,
        end_date=end_date,
        start_date=start_date,
        security_types=security_types,
        sleep_sec=sleep_sec,
        session=session,
        code_suffix=".SZ",
    )


if __name__ == "__main__":
    print(download_szse_trend())
