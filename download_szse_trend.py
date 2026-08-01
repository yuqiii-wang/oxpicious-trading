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
    "index": {
        "catalogid": "1815_stock_snapshot",
        "tabkey": "tab7",
        "prefix": "szse_trend_index",
    },
}

# Per-type identity tables for check_identity (DB-first download mode).
# code_suffix='SZ' filters to SZSE-only rows so multi-source tables
# (SZSE+SSE+BSE) are queried per-exchange.
# 'option' and 'index' have no identity table — fall back to filesystem scan.
DB_TABLE_BY_TYPE: Dict[str, str] = {
    "stock": "stats.stock_identity",
    "etf": "stats.etf_identity",
}

# Per-type CSV row filter. The index tab7 export contains ~180 indexes
# per day; only the two broad-market benchmarks below are persisted to CSV.
# The xlsx is always written in full; only the CSV is filtered.
INDEX_CODES_TO_KEEP: List[str] = ["399001", "399006", "399237"]  # 深证成指, 创业板指, 运输指数
CODE_FILTER_BY_TYPE: Dict[str, List[str]] = {
    "index": INDEX_CODES_TO_KEEP,
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
    sleep_sec: float = 5.0,
    session: Optional[requests.Session] = None,
) -> dict:
    """
    Download SZSE trend page market data day by day for stocks, ETFs/funds,
    options, and indexes.

    Uses CATALOGID=1815_stock_snapshot from
    https://www.szse.cn/market/trend/index.html with ``txtBeginDate`` and
    ``txtEndDate`` set to the **same** business date each request, walking
    backwards from *end_date* until *start_date* (default 2025-07-01).

    ``security_types`` defaults to ``["stock", "etf", "option", "index"]``.
    For the ``index`` type (TABKEY=tab7) the xlsx contains ~180 indexes per
    day; the CSV is filtered to only 399001 深证成指, 399006 创业板指,
    and 399237 运输指数 (see CODE_FILTER_BY_TYPE). The xlsx is kept in full.
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
        db_table_by_type=DB_TABLE_BY_TYPE,
        db_code_suffix="SZ",
        code_filter_by_type=CODE_FILTER_BY_TYPE,
    )


if __name__ == "__main__":
    print(download_szse_trend())
