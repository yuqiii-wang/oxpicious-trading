"""Download CSI index-linked ETF list from csindex.com.cn and filter by AUM > 1亿.

Studies ``https://www.csindex.com.cn/#/indices/indexProduct`` whose
"导出列表" (export list) button triggers two POST requests:

  1. ``POST /csindex-home/index-list/funds-tracking-index``
     Returns JSON paginated list of all funds tracking CSI indices.
  2. ``POST /csindex-home/exportExcel/funds-tracking-index-excel/CH``
     Returns an Excel (.xls) file with the same data.

The SPA (Vue.js) constructs the request body in its ``queryData`` computed
property (discovered in chunk-30627aa2.3c6a6aef.js):

  {
    "lang": "cn",
    "pager": {"pageNum": 1, "pageSize": 10000},
    "searchInput": null,          // null = all indices
    "sortField": null,
    "sortOrder": null,
    "fundsFilter": {
      "fundSize": null,           // null = no size filter
      "assetClass": null,
      "fundType": ["etf"],        // ["etf"] = ETFs only
      "coverage": null,
      "market": null,
      "fundAge": null,
      "manager": null
    }
  }

The ``aum`` field in the response is in 亿元 (hundred millions of CNY),
so ``aum > 1`` means > 1亿 (100 million CNY).

Output:
  ``temps/csindex_linked_etf/etf_index_map_all_<today>.csv`` — all ETFs (unfiltered)
  ``temps/csindex_linked_etf/etf_index_map_<today>.csv``     — ETFs with AUM > 1亿 (the deliverable)
  ``temps/csindex_linked_etf/etf_raw.xls``                   — raw Excel from the export endpoint

``<today>`` is the run date in ``YYYY-MM-DD`` format (e.g. ``2026-07-30``).

Month-start refresh:
  On the 1st day of each month the cache is bypassed and the ETF list is
  re-downloaded. On other days, if today's CSV already exists it is reused
  (skip re-download). Use --force-month-start to trigger the month-start
  behavior on any day for testing.

Usage:
  python download_csindex_linked_etf.py
  python download_csindex_linked_etf.py --min-aum 2.0   # AUM > 2亿
  python download_csindex_linked_etf.py --force-month-start
  python download_csindex_linked_etf.py --out-root /tmp/csindex_etf
"""
from __future__ import annotations


import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

from downloads._common import (
    DEFAULT_SLEEP_SEC,
    AntiBotConfig,
    AntiBotProxy,
    RunStats,
    build_default_session,
    is_error_html,
    is_valid_file,
    merge_browser_profile,
    resolve_out_dir,
    setup_logger,
)
from downloads._common.monthly import is_month_start

# ---------------------------------------------------------------------------
# csindex.com.cn API endpoints
# ---------------------------------------------------------------------------

CSINDEX_BASE = "https://www.csindex.com.cn"

# POST JSON list — paginated fund data
API_FUNDS_TRACKING_INDEX = CSINDEX_BASE + "/csindex-home/index-list/funds-tracking-index"

# POST Excel export — same body, returns .xls binary
API_EXPORT_FUNDS_EXCEL = CSINDEX_BASE + "/csindex-home/exportExcel/funds-tracking-index-excel/CH"

# GET filter options (fund size buckets, asset classes, fund types, etc.)
API_FUNDS_FILTER = CSINDEX_BASE + "/csindex-home/index-list/funds-filter"

PRODUCT_PAGE_URL = CSINDEX_BASE + "/#/indices/indexProduct"

CSINDEX_TIMEOUT: Tuple[int, int] = (15, 120)

# AUM threshold in 亿元 (1亿 = 100 million CNY)
DEFAULT_MIN_AUM = 1.0

# Large page size to fetch all ETFs in one request
FETCH_PAGE_SIZE = 10000

logger = setup_logger("csindex_linked_etf")

CSINDEX_HEADERS: Dict[str, str] = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Origin": CSINDEX_BASE,
    "Referer": PRODUCT_PAGE_URL,
}


# ---------------------------------------------------------------------------
# Request body builder — mirrors the SPA's queryData computed property
# ---------------------------------------------------------------------------

def build_request_body(
    *,
    fund_type: Optional[List[str]] = None,
    fund_size: Optional[List[str]] = None,
    search_input: Optional[str] = None,
    page_num: int = 1,
    page_size: int = FETCH_PAGE_SIZE,
    sort_field: Optional[str] = None,
    sort_order: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the POST body matching the SPA's queryData structure.

    All filter fields default to ``None`` (no filter). Pass a list of filter
    keys (from the funds-filter API) to activate a filter.
    """
    return {
        "lang": "cn",
        "pager": {"pageNum": page_num, "pageSize": page_size},
        "searchInput": search_input,
        "sortField": sort_field,
        "sortOrder": sort_order,
        "fundsFilter": {
            "fundSize": fund_size,
            "assetClass": None,
            "fundType": fund_type,
            "coverage": None,
            "market": None,
            "fundAge": None,
            "manager": None,
        },
    }


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_funds_list(
    session: requests.Session,
    proxy: AntiBotProxy,
    *,
    fund_type: Optional[List[str]] = None,
    search_input: Optional[str] = None,
    page_size: int = FETCH_PAGE_SIZE,
) -> List[Dict[str, Any]]:
    """Fetch the full fund list from the JSON API in a single large page.

    Returns a list of fund record dicts. Each record has fields:
    productCode, fundName, fundNameEn, assetClass, fundType, coverage,
    indexCode, indexNameCn, indexNameEn, aum (亿元), fundManager,
    inceptionDate, exchange, etc.
    """
    body = build_request_body(
        fund_type=fund_type,
        search_input=search_input,
        page_size=page_size,
    )

    resp = proxy.post(
        session,
        API_FUNDS_TRACKING_INDEX,
        data=json.dumps(body),
        headers=CSINDEX_HEADERS,
        timeout=CSINDEX_TIMEOUT,
        logger=logger,
        log_tag="[funds-list]",
    )
    if resp is None:
        logger.error("[funds-list] request failed (blocked or network error)")
        return []

    ctype = resp.headers.get("Content-Type", "")
    if is_error_html(ctype, resp.content):
        logger.error("[funds-list] got error HTML response (blocked?)")
        proxy.record_error(API_FUNDS_TRACKING_INDEX, 403, "error_html_detected")
        return []

    try:
        payload = resp.json()
    except ValueError as e:
        logger.error("[funds-list] JSON parse error: %s", e)
        return []

    if payload.get("code") != "200":
        logger.error("[funds-list] API error: code=%s msg=%s", payload.get("code"), payload.get("msg"))
        return []

    data = payload.get("data")
    if not isinstance(data, list):
        logger.warning("[funds-list] no data array in response")
        return []

    total = payload.get("total", 0)
    logger.info("[funds-list] fetched %d/%d records (pageSize=%d)", len(data), total, page_size)
    return data


def fetch_export_excel(
    session: requests.Session,
    proxy: AntiBotProxy,
    *,
    fund_type: Optional[List[str]] = None,
    search_input: Optional[str] = None,
    out_file: Path,
) -> bool:
    """Download the Excel export of the fund list and save to *out_file*.

    Returns True on success. The export endpoint returns a .xls binary
    with columns: 产品代码, 产品名称, 标的指数代码, 标的指数, 资产类别,
    产品类型, 上市地, 资产净值（亿元）, 成立日期, 管理人.
    """
    body = build_request_body(
        fund_type=fund_type,
        search_input=search_input,
    )

    resp = proxy.post(
        session,
        API_EXPORT_FUNDS_EXCEL,
        data=json.dumps(body),
        headers=CSINDEX_HEADERS,
        timeout=CSINDEX_TIMEOUT,
        logger=logger,
        log_tag="[export-excel]",
    )
    if resp is None:
        logger.error("[export-excel] request failed")
        return False

    ctype = resp.headers.get("Content-Type", "")
    content = resp.content

    # Successful export returns Excel binary
    is_xls = (
        content[:4] == b"PK\x03\x04"  # xlsx (zip)
        or content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # xls (OLE2)
        or "excel" in ctype.lower()
        or "octet-stream" in ctype.lower()
    )

    if not is_xls:
        # Likely a JSON error
        try:
            err = resp.json()
            logger.error("[export-excel] API error: code=%s msg=%s", err.get("code"), err.get("msg"))
        except (ValueError, AttributeError):
            logger.error("[export-excel] non-Excel response (ctype=%s, %d bytes)", ctype, len(content))
        return False

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(content)
    logger.info("[export-excel] saved %s (%d bytes)", out_file.name, len(content))
    return True


# ---------------------------------------------------------------------------
# Filtering and output
# ---------------------------------------------------------------------------

# Column mapping: API JSON field → output CSV column (Chinese, matching export)
COLUMN_MAP: Dict[str, str] = {
    "productCode": "产品代码",
    "fundName": "产品名称",
    "indexCode": "标的指数代码",
    "indexNameCn": "标的指数",
    "assetClass": "资产类别",
    "fundType": "产品类型",
    "exchange": "上市地",
    "aum": "资产净值（亿元）",
    "inceptionDate": "成立日期",
    "fundManager": "管理人",
}

OUTPUT_COLUMNS: List[str] = list(COLUMN_MAP.values())


def records_to_dataframe(records: List[Dict[str, Any]]) -> pd.DataFrame:
    """Convert API JSON records to a clean DataFrame with Chinese columns."""
    df = pd.DataFrame(records)
    # Rename and select columns
    for eng, chn in COLUMN_MAP.items():
        if eng in df.columns:
            df = df.rename(columns={eng: chn})
    # Keep only output columns that exist
    cols = [c for c in OUTPUT_COLUMNS if c in df.columns]
    df = df[cols]
    # Convert AUM to numeric (values are strings like "6.88")
    if "资产净值（亿元）" in df.columns:
        df["资产净值（亿元）"] = pd.to_numeric(df["资产净值（亿元）"], errors="coerce")
    return df


def filter_by_aum(df: pd.DataFrame, min_aum: float) -> pd.DataFrame:
    """Keep only rows where AUM (亿元) > *min_aum*."""
    col = "资产净值（亿元）"
    if col not in df.columns:
        logger.warning("[filter] AUM column '%s' not found, skipping filter", col)
        return df
    mask = df[col] > min_aum
    filtered = df[mask].reset_index(drop=True)
    logger.info("[filter] AUM > %s亿: kept %d / %d ETFs", min_aum, len(filtered), len(df))
    return filtered


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def download_csindex_linked_etf(
    *,
    out_root: Optional[str] = None,
    min_aum: float = DEFAULT_MIN_AUM,
    sleep_sec: float = DEFAULT_SLEEP_SEC,
    fetch_excel: bool = True,
    force_month_start: bool = False,
) -> dict:
    """Download all CSI index-linked ETFs and filter by AUM > *min_aum* (亿元).

    Flow:
      1. Fetch the full ETF list via JSON API (fundType=["etf"], all indices).
      2. Optionally download the raw Excel export for archival.
      3. Convert to DataFrame, save unfiltered ``etf_index_map_all_<today>.csv``.
      4. Filter by AUM > min_aum, save ``etf_index_map_<today>.csv`` (the deliverable).

    On non-month-start days, if today's deliverable CSV already exists it is
    reused (skip re-download). On the 1st of each month (or when
    *force_month_start* is set) the cache is bypassed.
    """
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "csindex_linked_etf", out_root)

    today_date = date.today()
    today = today_date.isoformat()
    month_start = force_month_start or is_month_start(today_date)

    # Cache check (non-month-start): reuse today's CSV if it already exists.
    if not month_start:
        cached_csv = out_dir / f"etf_index_map_{today}.csv"
        if is_valid_file(cached_csv, min_bytes=100):
            cached_all = out_dir / f"etf_index_map_all_{today}.csv"
            logger.info(
                "[cache] today's ETF list already downloaded: %s (use --force-month-start to re-download)",
                cached_csv.name,
            )
            return {
                "out_dir": str(out_dir),
                "total": 0,
                "filtered": 0,
                "cached": True,
                "cached_file": str(cached_csv),
                "cached_all_file": str(cached_all) if is_valid_file(cached_all, min_bytes=100) else None,
            }

    if month_start:
        logger.info(
            "Month-start refresh (today=%s, forced=%s): bypassing cache and "
            "re-downloading the ETF list.",
            today, force_month_start,
        )

    session = build_default_session(merge_browser_profile(CSINDEX_HEADERS))
    proxy = AntiBotProxy(AntiBotConfig(
        base_sleep_sec=sleep_sec,
        sleep_jitter=0.3,
    ))
    stats = RunStats()

    logger.info(
        "Starting csindex linked ETF download: min_aum=%s亿 out=%s",
        min_aum, out_dir,
    )

    # --- Step 1: Fetch full ETF list via JSON API ---
    records = fetch_funds_list(
        session, proxy,
        fund_type=["etf"],
        search_input=None,  # all indices
    )

    if not records:
        logger.error("No ETF records fetched, aborting")
        return {"out_dir": str(out_dir), "total": 0, "filtered": 0}

    stats.downloaded += 1

    # --- Step 2: Optional raw Excel export ---
    if fetch_excel:
        xls_path = out_dir / "etf_raw.xls"
        ok = fetch_export_excel(
            session, proxy,
            fund_type=["etf"],
            search_input=None,
            out_file=xls_path,
        )
        if ok:
            stats.files.append(str(xls_path))

    # --- Step 3: Convert and save unfiltered CSV ---
    df_all = records_to_dataframe(records)
    all_csv_path = out_dir / f"etf_index_map_all_{today}.csv"
    df_all.to_csv(all_csv_path, index=False, encoding="utf-8-sig")
    logger.info("[save] %s (%d ETFs)", all_csv_path.name, len(df_all))
    stats.files.append(str(all_csv_path))

    # --- Step 4: Filter by AUM and save the deliverable ---
    df_filtered = filter_by_aum(df_all, min_aum)
    etf_csv_path = out_dir / f"etf_index_map_{today}.csv"
    df_filtered.to_csv(etf_csv_path, index=False, encoding="utf-8-sig")
    logger.info("[save] %s (%d ETFs with AUM > %s亿)", etf_csv_path.name, len(df_filtered), min_aum)
    stats.files.append(str(etf_csv_path))

    summary = stats.to_dict(
        out_dir=str(out_dir),
        total_etfs=len(df_all),
        filtered_etfs=len(df_filtered),
        min_aum=min_aum,
    )
    logger.info(
        "Done. total=%d filtered=%d (AUM > %s亿) out=%s",
        len(df_all), len(df_filtered), min_aum, out_dir,
    )
    return summary


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Download CSI index-linked ETF list and filter by AUM > 1亿 (100 million CNY).",
    )
    ap.add_argument("--out-root", type=str, default=None,
                    help="Alternative output root directory")
    ap.add_argument("--min-aum", type=float, default=DEFAULT_MIN_AUM,
                    help=f"Minimum AUM in 亿元 (default: {DEFAULT_MIN_AUM} = 1亿/100mil CNY)")
    ap.add_argument("--sleep-sec", type=float, default=DEFAULT_SLEEP_SEC,
                    help=f"Sleep seconds between requests (default: {DEFAULT_SLEEP_SEC})")
    ap.add_argument("--no-excel", action="store_true", default=False,
                    help="Skip downloading the raw Excel export")
    ap.add_argument("--force-month-start", action="store_true", default=False,
                    help="Force month-start behavior: bypass cache and re-download "
                         "the ETF list. For testing the monthly refresh flow on any day.")
    args = ap.parse_args()

    result = download_csindex_linked_etf(
        out_root=args.out_root,
        min_aum=args.min_aum,
        sleep_sec=args.sleep_sec,
        fetch_excel=not args.no_excel,
        force_month_start=args.force_month_start,
    )
    print(result)
