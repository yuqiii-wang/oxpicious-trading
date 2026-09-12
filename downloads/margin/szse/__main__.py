
import random
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import requests

from downloads._common.exchanges.szse import (
    REFERER_MARGIN,
    build_headers,
    run_szse_download,
)
from downloads._common import (
    DEFAULT_START_DATE,
    last_business_day,
    single_instance_lock,
)

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("szse")


REPORT_CFGS: Dict[str, Dict[str, str]] = {
    "summary": {
        "catalogid": "1837_xxpl",
        "tabkey": "tab1",
        "prefix": "szse_margin_summary",
    },
    "detail": {
        "catalogid": "1837_xxpl",
        "tabkey": "tab2",
        "prefix": "szse_margin_detail",
    },
}

MARGIN_HEADERS = build_headers(REFERER_MARGIN)


def _build_margin_params(report_type: str, trade_date: date) -> Dict[str, object]:
    cfg = REPORT_CFGS[report_type]
    date_str = trade_date.strftime("%Y-%m-%d")
    return {
        "SHOWTYPE": "xlsx",
        "CATALOGID": cfg["catalogid"],
        "TABKEY": cfg["tabkey"],
        "txtDate": date_str,
        "random": random.random(),
    }


def _margin_log_tag(report_type: str, ymd: str) -> str:
    return f"[margin-{report_type} {ymd}]"


def download_szse_margin(
    out_root: Optional[str] = None,
    end_date: Optional[str] = None,
    start_date: str = DEFAULT_START_DATE,
    report_types: Optional[List[str]] = None,
    sleep_sec: float = 5.0,
    session: Optional[requests.Session] = None,
) -> dict:
    """
    Download SZSE margin (融资融券) data day by day, covering both the
    market-wide summary and per-security detail report.

    Uses CATALOGID=1837_xxpl from
    https://www.szse.cn/disclosure/margin/margin/index.html with ``txtDate``
    set to the target business date per request, walking backwards from
    *end_date* until *start_date* (default: DEFAULT_START_DATE in
    _download_commons, currently 2020-01-01).

    ``report_types`` defaults to ``["summary", "detail"]``:

    * ``summary`` (tab1) — 融资融券交易总量 — single market-wide row
      (融资买入额, 融资余额, 融券余量, 融券余额, 融资融券余额, ...)
    * ``detail``  (tab2) — 融资融券交易明细 — one row per underlying security
      (证券代码, 证券简称, 融资买入额, 融资余额, 融券卖出量, 融券余量, ...)

    Note: When ``end_date`` is not specified, the download window extends to
    the most recent trading day. SZSE publishes trade-date-T margin exports
    on T+1 (daytime), so the newest date may legitimately have no data yet
    when this runs late at night or early morning; such fetches write
    0-byte empty markers which are retried on subsequent runs within
    ``EMPTY_MARKER_RETRY_DAYS`` (see ``run_szse_download`` /
    ``build_day_download_plan``), so any run after publication picks the
    date up automatically — weekends included.
    """
    if end_date is None:
        effective_end_date = last_business_day(date.today()).isoformat()
    else:
        effective_end_date = end_date

    if report_types is None:
        security_types = None
    else:
        security_types = list(report_types)

    return run_szse_download(
        caller_file=str(Path(__file__).resolve()),
        out_dirname="szse_margin",
        banner_label="margin",
        security_cfgs=REPORT_CFGS,
        headers=MARGIN_HEADERS,
        params_builder=_build_margin_params,
        log_tag_fn=_margin_log_tag,
        out_root=out_root,
        end_date=effective_end_date,
        start_date=start_date,
        security_types=security_types,
        sleep_sec=sleep_sec,
        session=session,
        exchange="SZ",
        skip_empty_markers=True,
    )


if __name__ == "__main__":
    try:
        with single_instance_lock("downloads.margin.szse"):
            logger.info(download_szse_margin())
    except RuntimeError as exc:
        logger.warning("%s — exiting", exc)
