"""Download SZSE index (tab7) trend market data day by day.

Uses CATALOGID=1815_stock_snapshot, TABKEY=tab7 from
https://www.szse.cn/market/trend/index.html. Writes
``szse_trend_index_{YYYYMMDD}.xlsx/.csv`` under ``temps/sse_trend/``.

The xlsx contains ~180 indexes per day; the CSV is filtered to only
399001 深证成指, 399006 创业板指, 399348 深证价值, and
399346 深证成长. The xlsx is kept in full.

DB-first mode: queries ``stats.index_identity`` to find missing trading days,
skipping dates already in the DB. Additionally, ``skip_empty_markers=True``
excludes dates that have a local 0-byte CSV marker (previously fetched but
the server returned no data), preventing re-downloading those dates.
"""
from __future__ import annotations

import random
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import requests

from downloads._common.szse_runner import (
    REFERER_TREND,
    build_headers,
    run_szse_download,
)


TREND_ARCHIVE_DATE = "2025-07-01"

SECURITY_CFGS: Dict[str, Dict[str, str]] = {
    "index": {
        "catalogid": "1815_stock_snapshot",
        "tabkey": "tab7",
        "prefix": "szse_trend_index",
    },
}

# Per-type CSV row filter. The index tab7 export contains ~180 indexes
# per day; only the broad-market benchmarks below are persisted to CSV.
INDEX_CODES_TO_KEEP: List[str] = ["399001", "399006", "399348", "399346"]
CODE_FILTER_BY_TYPE: Dict[str, List[str]] = {
    "index": INDEX_CODES_TO_KEEP,
}

# DB-first mode: stats.index_identity has NO code_suffix column (codes are
# bare 6-digit like "399001"), so db_code_suffix is intentionally omitted.
DB_TABLE_BY_TYPE: Dict[str, str] = {
    "index": "stats.index_identity",
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


def download_szse_trend_index(
    out_root: Optional[str] = None,
    end_date: Optional[str] = None,
    start_date: str = "2025-07-01",
    sleep_sec: float = 5.0,
    session: Optional[requests.Session] = None,
) -> dict:
    return run_szse_download(
        caller_file=str(Path(__file__).resolve()),
        out_dirname="szse_trend",
        banner_label="trend-index",
        security_cfgs=SECURITY_CFGS,
        headers=TREND_HEADERS,
        params_builder=_build_trend_params,
        log_tag_fn=_trend_log_tag,
        out_root=out_root,
        end_date=end_date,
        start_date=start_date,
        security_types=["index"],
        sleep_sec=sleep_sec,
        session=session,
        code_suffix=".SZ",
        db_table_by_type=DB_TABLE_BY_TYPE,
        code_filter_by_type=CODE_FILTER_BY_TYPE,
        skip_empty_markers=True,
    )


if __name__ == "__main__":
    print(download_szse_trend_index())
