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
INDEX_EXTRA_PARAMS: Dict[str, Optional[str]] = {}

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
    "index": {
        "catalogid": "1815_stock",
        "tabkey": "tab7",
        "prefix": "szse_index",
        "extra": INDEX_EXTRA_PARAMS,
    },
}

# Per-type identity tables for check_identity (DB-first download mode).
# code_suffix='SZ' filters to SZSE-only rows so multi-source tables
# (SZSE+SSE+BSE) are queried per-exchange.
# 'index' has no identity table — falls back to filesystem scan.
DB_TABLE_BY_TYPE: Dict[str, str] = {
    "stock": "stats.stock_identity",
    "etf": "stats.etf_identity",
}

# Per-type CSV row filter. The index tab7 export contains ~180 indexes
# per day; only the two broad-market benchmarks below are persisted to CSV.
# The xlsx is always written in full; only the CSV is filtered.
INDEX_CODES_TO_KEEP: List[str] = ["399001", "399006"]  # 深证成指, 创业板指
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


def download_szse_archive(
    out_root: Optional[str] = None,
    end_date: Optional[str] = None,
    start_date: str = DEFAULT_START_DATE,
    security_types: Optional[List[str]] = None,
    sleep_sec: float = 5.0,
    session: Optional[requests.Session] = None,
) -> dict:
    """
    Download SZSE archive market data day by day for stocks, ETFs/funds,
    and indexes.

    Each date is requested individually against
    https://www.szse.cn/market/trend/archive/index.html until start_date is
    reached (default: DEFAULT_START_DATE in _download_commons, currently
    2020-01-01).

    ``security_types`` defaults to ``["stock", "etf", "index"]``. For the
    ``index`` type (CATALOGID=1815_stock, TABKEY=tab7) the xlsx contains
    ~180 indexes per day; the CSV is filtered to only 399001 深证成指 and
    399006 创业板指 (see CODE_FILTER_BY_TYPE). The xlsx is kept in full.
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
        db_table_by_type=DB_TABLE_BY_TYPE,
        db_code_suffix="SZ",
        code_filter_by_type=CODE_FILTER_BY_TYPE,
    )


if __name__ == "__main__":
    print(download_szse_archive())
