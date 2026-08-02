"""
download_szse_index_composition.py — Download SZSE index composition
(constituent stocks) from szse.cn and convert to CSV.

For each index code (e.g. 399001 深证成指, 399006 创业板指, 399237 运输指数), downloads
the latest constituent list from the SZSE "指数样本股" (index constituents)
API and computes approximate weights from float shares.

API endpoint:
  http://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1747
  &TABKEY=tab1&PAGENO={page}&ZSDM={index_code}

Each page returns up to 10 records. The API returns the LATEST snapshot
when filtered by ZSDM (index code).

The downloaded data includes:
  - zqdm: stock code (6-digit)
  - zqjc: stock name
  - zgb: total shares (总股本)
  - ltgb: float shares (流通股本)
  - hylb: industry
  - nrzsjs: calculation flag (1=included in index calculation)

Weights are computed as: weight_pct = ltgb / sum(ltgb for nrzsjs=1 stocks)

Output CSV schema (matches what build_szse_sse_etf_and_margin.py expects):
  snapshot_date, index_code, index_name, stock_code, stock_name, weight_pct

Month-start refresh:
  On the 1st day of each month the cache is bypassed and every index is
  re-downloaded. The CSV is stamped with TODAY's date (not the API's
  snapshot_date, which typically reflects the previous business day — e.g.
  running on 2026-08-01 yields a CSV dated 20260801 even though the API
  reports 2026-07-31). This ensures a fresh monthly snapshot flows through
  to prod (stats.sec_composition) under the new month's date. Use
  --force-month-start to trigger this behavior on any day for testing.

Usage:
  python download_szse_index_composition.py
  python download_szse_index_composition.py --index-codes 399001,399006,399237
  python download_szse_index_composition.py --skip-cached
  python download_szse_index_composition.py --force-month-start
  python download_szse_index_composition.py --out-root /tmp/szse_comp
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from downloads._common.core import (
    DEFAULT_TIMEOUT,
    DEFAULT_SHORT_SLEEP_SEC,
    AntiBotProxy,
    AntiBotConfig,
    setup_logger,
    resolve_out_dir,
    is_valid_file,
    build_default_session,
    RunStats,
    random_browser_profile,
    add_exchange_suffix,
    get_exchange_from_code,
)
from downloads._common.monthly import is_month_start

logger = setup_logger("szse_index_composition")

SZSE_INDEX_API = (
    "http://www.szse.cn/api/report/ShowReport/data"
)

# Default indices to track
DEFAULT_INDEX_CODES = ["399001", "399006", "399237", "399812"]

# Sleep between API calls (SZSE API is less aggressive than the report site)
SLEEP_SEC = DEFAULT_SHORT_SLEEP_SEC
PAGE_SIZE = 10


def _get_szse_index_name(index_code: str) -> str:
    """Return the Chinese name for a known SZSE index code."""
    _NAMES = {
        "399001": "深证成指",
        "399006": "创业板指",
        "399004": "深证100",
        "399100": "深证300",
        "399106": "深证600",
        "399305": "深证50",
        "399673": "创业板50",
        "399674": "创业板红利",
        "399005": "中小板指",
        "399007": "深证300",
        "399008": "深证创新",
        "399009": "深证环保",
        "399010": "深证龙头",
        "399011": "深证治理",
        "399015": "农业指数",
        "399016": "深证价值",
        "399017": "深证成长",
        "399018": "深证红利",
        "399019": "深证价格",
        "399020": "深证高股息",
        "399025": "央企改革",
        "399026": "深证国资",
        "399032": "国证转债",
        "399033": "上证可转债",
        "399037": "创业板科技",
        "399041": "国证新能源车",
        "399042": "国证食品饮料",
        "399043": "国证电子",
        "399046": "国证基建",
        "399047": "国证金融",
        "399048": "国证地产",
        "399049": "国证医药",
        "399050": "国证农林牧渔",
        "399051": "国证通信",
        "399052": "国证银行",
        "399053": "国证媒体",
        "399054": "国证水电燃气",
        "399055": "国证航空航天",
        "399056": "国证石油石化",
        "399057": "国证商贸零售",
        "399058": "国证家电",
        "399059": "国证机械设备",
        "399060": "国证汽车",
        "399061": "国证化工",
        "399062": "国证建材",
        "399063": "国证钢铁",
        "399064": "国证轻工制造",
        "399065": "国证社会服务",
        "399066": "国证纺织服饰",
        "399067": "国证国防军工",
        "399068": "国证煤炭",
        "399069": "国证环保",
        "399070": "国证非银金融",
        "399071": "国证计算机",
        "399072": "国证电力设备",
        "399073": "国证国防军工",
        "399074": "国证建材",
        "399075": "国证电气设备",
        "399076": "国证传媒",
        "399077": "国证通信",
        "399078": "国证银行",
        "399079": "国证综合",
        "399080": "国证美容护理",
        "399081": "国证电子",
        "399082": "国证石化",
        "399083": "国证基础化工",
        "399084": "国证环保",
        "399085": "国证机械设备",
        "399086": "国证家用电器",
        "399087": "国证食品饮料",
        "399088": "国证通信",
        "399089": "国证化工",
        "399090": "国证社会服务",
        "399091": "国证汽车",
        "399092": "国证纺织服饰",
        "399093": "国证医药生物",
        "399094": "国证公用事业",
        "399095": "国证国防军工",
        "399096": "国证农林牧渔",
        "399097": "国证商贸零售",
        "399098": "国证轻工制造",
        "399099": "国证综合",
        "399100": "国证A500",
        "399101": "国证A200",
        "399102": "国证A50",
        "399103": "深证100",
        "399106": "深证600",
        "399305": "深证50",
        "399673": "创业板50",
        "399674": "创业板红利",
        "399678": "国证H股",
        "399685": "恒生指数",
        "399975": "证券公司",
        "399986": "中证银行",
        "399989": "中证医疗",
        "399997": "中证白酒",
        "399998": "中证煤炭",
        "399999": "中证红利",
        "399001": "深证成指",
        "399006": "创业板指",
        "399237": "运输指数",
    }
    return _NAMES.get(index_code, f"SZSE-{index_code}")


def _parse_subname_date(subname: str) -> Optional[str]:
    """Extract the snapshot date from the subname field.

    Expected format: "399001 深证成份指数 2026-07-29" or similar.
    Returns YYYY-MM-DD string or None if parsing fails.
    """
    if not subname:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", str(subname))
    if m:
        return m.group(1)
    return None


def fetch_index_page(
    session: requests.Session,
    index_code: str,
    page_no: int,
    base_headers: Dict[str, str],
    proxy: Optional[AntiBotProxy] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch a single page of index composition data from SZSE API.

    Returns the parsed JSON response dict, or None on failure.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=SLEEP_SEC))

    params: Dict[str, Any] = {
        "SHOWTYPE": "JSON",
        "CATALOGID": "1747",
        "TABKEY": "tab1",
        "PAGENO": page_no,
        "ZSDM": index_code,
    }
    if page_no == 1:
        params["loading"] = "first"

    for attempt in range(1, 5):
        resp = proxy.get(
            session,
            SZSE_INDEX_API,
            params=params,
            headers=base_headers,
            timeout=DEFAULT_TIMEOUT,
            logger=logger,
            log_tag=f"[szse-index {index_code} p{page_no}]",
        )
        if resp is None:
            if attempt == 4:
                logger.error(
                    "[szse-index %s p%d] fetch error after 4 attempts",
                    index_code, page_no,
                )
                return None
            backoff = 2.0 * attempt
            logger.warning(
                "[szse-index %s p%d] attempt %d failed, retry in %.1fs",
                index_code, page_no, attempt, backoff,
            )
            proxy.sleep(backoff)
            continue

        try:
            payload = resp.json()
        except ValueError as e:
            if attempt == 4:
                logger.error(
                    "[szse-index %s p%d] json parse error: %s",
                    index_code, page_no, e,
                )
                return None
            backoff = 2.0 * attempt
            proxy.sleep(backoff)
            continue

        return payload

    return None


def fetch_all_pages_for_index(
    session: requests.Session,
    index_code: str,
    base_headers: Dict[str, str],
    proxy: Optional[AntiBotProxy] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch all pages of composition data for a single index.

    Returns a dict with:
      - snapshot_date: str
      - index_code: str
      - index_name: str
      - constituents: list of dicts with keys:
          stock_code, stock_name, zgb, ltgb, industry, calc_flag
    Returns None on failure.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=SLEEP_SEC))

    # Fetch first page to get metadata
    first_page = fetch_index_page(session, index_code, 1, base_headers, proxy)
    if first_page is None:
        logger.error("[%s] Failed to fetch first page", index_code)
        return None

    if isinstance(first_page, list) and first_page:
        item0 = first_page[0]
    elif isinstance(first_page, dict):
        item0 = first_page
    else:
        logger.error("[%s] Unexpected response type: %s", index_code, type(first_page))
        return None

    metadata = item0.get("metadata") or {}
    pagecount = int(metadata.get("pagecount") or 0)
    recordcount = int(metadata.get("recordcount") or 0)
    subname = metadata.get("subname", "")

    snapshot_date = _parse_subname_date(subname)
    if not snapshot_date:
        logger.error("[%s] Could not parse snapshot_date from subname: %s", index_code, subname)
        return None

    index_name = _get_szse_index_name(index_code)
    # Try to extract a more specific name from subname
    m = re.match(r"^\d+\s+(.+?)\s+\d{4}-\d{2}-\d{2}$", subname)
    if m:
        index_name = m.group(1).strip()

    logger.info(
        "[%s] %s: %d records, %d pages, snapshot=%s",
        index_code, index_name, recordcount, pagecount, snapshot_date,
    )

    all_data: List[Dict[str, Any]] = list(item0.get("data") or [])

    for pno in range(2, pagecount + 1):
        if proxy.is_blocked(SZSE_INDEX_API):
            logger.warning("[%s] szse.cn blocked, stopping pagination at page %d",
                           index_code, pno)
            break

        page_data = fetch_index_page(session, index_code, pno, base_headers, proxy)
        if page_data is None:
            logger.warning("[%s] Failed to fetch page %d, continuing", index_code, pno)
            continue

        if isinstance(page_data, list) and page_data:
            page_item = page_data[0]
        elif isinstance(page_data, dict):
            page_item = page_data
        else:
            continue

        page_rows = page_item.get("data") or []
        all_data.extend(page_rows)

        logger.info("[%s] p%d/%d: %d rows (cumulative: %d)",
                    index_code, pno, pagecount, len(page_rows), len(all_data))
        proxy.sleep(SLEEP_SEC)

    if not all_data:
        logger.warning("[%s] No constituent data found", index_code)
        return None

    constituents = []
    for row in all_data:
        if not isinstance(row, dict):
            continue
        zqdm = str(row.get("zqdm", "")).strip()
        if not zqdm or not zqdm.isdigit() or len(zqdm) != 6:
            continue

        # Determine exchange from code:
        # 000xxx / 300xxx / 301xxx -> SZ
        # 600xxx / 601xxx / 603xxx / 605xxx / 688xxx -> SS
        # 8xxxxx -> BJ
        exchange = get_exchange_from_code(zqdm)
        if exchange:
            stock_code = f"{zqdm}.{exchange}"
        else:
            stock_code = zqdm  # keep bare code if exchange unknown

        constituents.append({
            "stock_code": stock_code,
            "stock_name": str(row.get("zqjc", "")).strip(),
            "zgb": str(row.get("zgb", "")).replace(",", "").strip(),
            "ltgb": str(row.get("ltgb", "")).replace(",", "").strip(),
            "industry": str(row.get("hylb", "")).strip(),
            "calc_flag": str(row.get("nrzsjs", "")).strip(),
        })

    # Compute weights based on float shares (ltgb)
    # Only include stocks with calc_flag=1 (included in index calculation)
    ltgb_values = []
    for c in constituents:
        if c["calc_flag"] == "1" and c["ltgb"]:
            try:
                ltgb_values.append(float(c["ltgb"]))
            except ValueError:
                pass

    total_ltgb = sum(ltgb_values) if ltgb_values else 1.0

    for c in constituents:
        if c["calc_flag"] == "1" and c["ltgb"]:
            try:
                ltgb_val = float(c["ltgb"])
                c["weight_pct"] = round((ltgb_val / total_ltgb) * 100.0, 6)
            except ValueError:
                c["weight_pct"] = 0.0
        else:
            c["weight_pct"] = 0.0

    # Sort by weight descending
    constituents.sort(key=lambda x: x["weight_pct"], reverse=True)

    logger.info("[%s] %d constituents, sum(weights)=%.4f%%",
                index_code, len(constituents),
                sum(c["weight_pct"] for c in constituents))

    return {
        "snapshot_date": snapshot_date,
        "index_code": index_code,
        "index_name": index_name,
        "constituents": constituents,
    }


def save_to_csv(
    result: Dict[str, Any],
    out_dir: Path,
    override_date: Optional[str] = None,
) -> Path:
    """Save the downloaded composition data to a CSV file.

    CSV schema: snapshot_date, index_code, index_name, stock_code, stock_name, weight_pct

    When *override_date* is set (YYYY-MM-DD), it replaces the API's
    snapshot_date in both the DataFrame and the filename — used by the
    month-start refresh to stamp the CSV with today's date.
    """
    snapshot_date = override_date or result["snapshot_date"]
    index_code = result["index_code"]
    index_name = result["index_name"]
    constituents = result["constituents"]

    df = pd.DataFrame(constituents)
    if df.empty:
        raise ValueError(f"No constituent data for {index_code}")

    df["snapshot_date"] = snapshot_date
    df["index_code"] = index_code
    df["index_name"] = index_name

    # Select and order columns
    out_cols = [
        "snapshot_date", "index_code", "index_name",
        "stock_code", "stock_name", "weight_pct",
    ]
    # Add extra columns for debugging
    for col in ["zgb", "ltgb", "industry", "calc_flag"]:
        if col in df.columns:
            out_cols.append(col)

    df = df[out_cols]

    ymd = snapshot_date.replace("-", "")
    csv_name = f"{index_code}_closeweight_{ymd}.csv"
    csv_path = out_dir / csv_name
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info("  [saved] %s (%d constituents)%s", csv_name, len(df),
                f" (month-start stamp={override_date})" if override_date else "")
    return csv_path


def download_szse_index_composition(
    *,
    index_codes: Optional[List[str]] = None,
    out_root: Optional[str] = None,
    skip_cached: bool = True,
    sleep_sec: float = SLEEP_SEC,
    force_month_start: bool = False,
) -> dict:
    """Download SZSE index composition data for the given index codes.

    Args:
        index_codes: list of 6-digit SZSE index codes (default: DEFAULT_INDEX_CODES)
        out_root: alternative output root directory
        skip_cached: skip indices that already have a cached CSV
        sleep_sec: sleep between downloads
        force_month_start: force month-start behavior (bypass cache + stamp
            CSVs with today's date) regardless of the actual calendar day.

    Returns:
        Summary dict with download stats and output directory.
    """
    out_dir = resolve_out_dir(
        str(Path(__file__).resolve()), "szse_index_composition", out_root
    )

    if not index_codes:
        index_codes = list(DEFAULT_INDEX_CODES)

    if not index_codes:
        logger.warning("No index codes to download")
        return {"out_dir": str(out_dir), "downloaded": 0, "skipped_cached": 0, "failed": 0}

    # Month-start trigger: on the 1st of each month (or when forced), bypass
    # the cache and stamp CSVs with today's date so a fresh monthly snapshot
    # flows to prod even if the API still reports the previous biz day.
    today = date.today()
    month_start = force_month_start or is_month_start(today)
    if month_start:
        skip_cached = False
        logger.info(
            "Month-start refresh (today=%s, forced=%s): bypassing cache and "
            "stamping CSVs with today's date (overrides API snapshot_date).",
            today.isoformat(), force_month_start,
        )

    logger.info(
        "Starting SZSE index composition download: %d indices, out=%s",
        len(index_codes), out_dir,
    )

    session = build_default_session()
    stats = RunStats()
    proxy_config = AntiBotConfig(base_sleep_sec=sleep_sec)
    proxy = AntiBotProxy(proxy_config)

    results: List[Dict[str, Any]] = []

    try:
        for code in index_codes:
            code = str(code).strip()
            if not code:
                continue

            name = _get_szse_index_name(code)
            logger.info("== Index %s (%s) ==", code, name)

            # Check cache (skipped during month-start refresh — every index is
            # re-downloaded so the new month's snapshot is created in prod).
            if not month_start:
                csv_files = sorted(out_dir.glob(f"{code}_closeweight_*.csv"))
                if skip_cached and csv_files:
                    latest_csv = csv_files[-1]
                    if is_valid_file(latest_csv, min_bytes=100):
                        logger.info("  [cache] %s already cached: %s", code, latest_csv.name)
                        stats.skipped_cached += 1
                        results.append({
                            "code": code, "name": name,
                            "status": "cached", "file": str(latest_csv),
                        })
                        continue

            if proxy.is_blocked(SZSE_INDEX_API):
                logger.warning("  [host-blocked] szse.cn blocked, skipping %s", code)
                stats.failed += 1
                results.append({"code": code, "name": name, "status": "blocked"})
                continue

            browser_profile = random_browser_profile()

            # Fetch all pages
            result = fetch_all_pages_for_index(
                session, code, browser_profile, proxy
            )
            if result is None:
                stats.failed += 1
                results.append({
                    "code": code, "name": name,
                    "status": "download_failed",
                })
                continue

            # Save to CSV. On month-start refresh, stamp the CSV with today's
            # date (overriding the API's snapshot_date, which is usually the
            # previous business day) so a fresh monthly snapshot flows to
            # prod (stats.sec_composition) under the new month's date.
            out_dir.mkdir(parents=True, exist_ok=True)
            override_date = today.isoformat() if month_start else None
            csv_path = save_to_csv(result, out_dir, override_date=override_date)

            stats.downloaded += 1
            stats.files.append(str(csv_path))
            results.append({
                "code": code,
                "name": name,
                "status": "downloaded",
                "file": str(csv_path),
                "n_constituents": len(result["constituents"]),
                "snapshot_date": override_date or result["snapshot_date"],
                "month_start": month_start,
            })

            proxy.sleep(sleep_sec)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = stats.to_dict(
        out_dir=str(out_dir),
        n_indices=len(index_codes),
        results=results,
    )
    logger.info(
        "Done SZSE index composition. downloaded=%d skipped_cached=%d failed=%d",
        stats.downloaded, stats.skipped_cached, stats.failed,
    )
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download SZSE index composition (constituent stocks) from szse.cn."
    )
    ap.add_argument(
        "--index-codes", type=str, default=None,
        help="Comma-separated list of index codes (default: 399001,399006,399237). "
             "Example: --index-codes 399001,399006,399004",
    )
    ap.add_argument(
        "--out-root", type=str, default=None,
        help="Alternative output root directory",
    )
    ap.add_argument(
        "--no-skip-cached", action="store_true", default=False,
        help="Re-download even if a cached CSV exists",
    )
    ap.add_argument(
        "--force-month-start", action="store_true", default=False,
        help="Force month-start behavior: bypass cache and stamp CSVs with "
             "today's date (overrides API snapshot_date). For testing the "
             "monthly refresh flow on any day.",
    )
    ap.add_argument(
        "--sleep-sec", type=float, default=SLEEP_SEC,
        help=f"Sleep seconds between downloads (default: {SLEEP_SEC})",
    )
    args = ap.parse_args()

    codes = None
    if args.index_codes:
        codes = [c.strip() for c in args.index_codes.split(",") if c.strip()]

    result = download_szse_index_composition(
        index_codes=codes,
        out_root=args.out_root,
        skip_cached=not args.no_skip_cached,
        sleep_sec=args.sleep_sec,
        force_month_start=args.force_month_start,
    )
    print(result)