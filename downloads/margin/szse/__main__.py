import random
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests

from downloads._common.szse_runner import (
    BASE_URL,
    REFERER_MARGIN,
    build_headers,
    run_szse_download,
    resolve_out_dir,
)
from downloads._common.core import (
    MIN_VALID_BYTES,
    EMPTY_HTML_MAX_BYTES,
    DEFAULT_START_DATE,
    is_trading_day,
    is_valid_file,
    is_error_html,
    safe_write_bytes,
)


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


def _prev_business_day(ref: Optional[date] = None, skip_days: int = 1) -> date:
    d = ref if ref is not None else date.today()
    count = 0
    while count < skip_days:
        d -= timedelta(days=1)
        if is_trading_day(d):
            count += 1
    return d


def _is_margin_data_available(
    check_date: date,
    out_dir: Path,
    session: Optional[requests.Session] = None,
) -> bool:
    ymd = check_date.strftime("%Y%m%d")
    out_file = out_dir / f"szse_margin_summary_{ymd}.xlsx"

    if is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
        csv_path = out_file.with_suffix(".csv")
        if csv_path.exists():
            try:
                df = pd.read_csv(csv_path, encoding="utf-8-sig")
                if len(df) > 0:
                    return True
            except Exception:
                pass
        try:
            df = pd.read_excel(out_file, sheet_name=0)
            if isinstance(df, pd.DataFrame) and len(df) > 0:
                return True
        except Exception:
            pass
        return False

    sess = session or requests.Session()
    params = _build_margin_params("summary", check_date)
    headers = build_headers(REFERER_MARGIN)

    try:
        resp = sess.get(BASE_URL, params=params, headers=headers, timeout=(15, 60))
        resp.raise_for_status()
    except requests.RequestException:
        return False

    content_type = resp.headers.get("Content-Type", "")
    if is_error_html(content_type, resp.content, max_html_bytes=EMPTY_HTML_MAX_BYTES):
        return False

    if len(resp.content) < MIN_VALID_BYTES:
        return False

    try:
        df = pd.read_excel(BytesIO(resp.content), sheet_name=0)
        if isinstance(df, pd.DataFrame) and len(df) > 0:
            safe_write_bytes(out_file, resp.content, min_bytes=MIN_VALID_BYTES)
            return True
    except Exception:
        pass

    return False


def _find_best_margin_end_date(
    out_dir: Path,
    session: Optional[requests.Session] = None,
) -> date:
    now = datetime.now()
    today = date.today()

    if is_trading_day(today) and now.hour >= 10:
        if _is_margin_data_available(today, out_dir, session):
            return today

    for skip in [1, 2]:
        candidate = _prev_business_day(today, skip_days=skip)
        if _is_margin_data_available(candidate, out_dir, session):
            return candidate

    return _prev_business_day(today, skip_days=2)


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

    Note: When ``end_date`` is not specified, the function automatically
    determines the best available date:
    - If it's after 15:00 on a trading day, first check if today's data is available
    - If today's data is empty/not available, try 1 business day prior
    - If still not available, try 2 business days prior
    """
    if end_date is None:
        out_dir = resolve_out_dir(str(Path(__file__).resolve()), "szse_margin", out_root)
        best_date = _find_best_margin_end_date(out_dir, session)
        effective_end_date = best_date.strftime("%Y-%m-%d")
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
        code_suffix=".SZ",
    )


if __name__ == "__main__":
    print(download_szse_margin())
