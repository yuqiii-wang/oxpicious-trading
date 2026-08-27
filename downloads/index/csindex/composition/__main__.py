"""
download_index_composition.py — Download CSI index composition (closeweight)
from csindex.com.cn and convert to CSV.

For each index code, downloads the latest constituent close-weight xls from:
  https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/closeweight/{code}closeweight.xls

The xls is a single-snapshot file (latest rebalance date only) with bilingual
headers. Each row = one constituent stock with its closing weight (%).

The downloaded xls is parsed (pd.read_excel with HTML fallback) and saved as:
  temps/csi_index_composition/{code}_closeweight_{YYYYMMDD}.csv

CSV schema (matches what build_szse_sse_etf_and_margin.py expects):
  snapshot_date, index_code, index_name, stock_code, stock_name, weight_pct

  - snapshot_date: parsed from the xls's 日期 column (YYYYMMDD → YYYY-MM-DD)
  - stock_code:    bare 6-digit code + exchange suffix (.SS/.SZ) derived from
                   the 交易所 column
  - weight_pct:    float, the closing weight percentage (e.g. 10.335)

Month-start refresh:
  On the 1st day of each month the cache is bypassed and every index is
  re-downloaded. The CSV is stamped with TODAY's date (not the xls's snapshot
  date, which typically reflects the previous business day — e.g. running on
  2026-08-01 yields a CSV dated 20260801 even though the xls reports
  2026-07-31). This ensures a fresh monthly snapshot flows through to prod
  (stats.sec_composition) under the new month's date. Use --force-month-start
  to trigger this behavior on any day for testing.

Usage:
  python download_index_composition.py
  python download_index_composition.py --index-codes 930606,000300
  python download_index_composition.py --skip-cached
  python download_index_composition.py --force-month-start
  python download_index_composition.py --out-root /tmp/csi_comp
"""
from __future__ import annotations


import argparse
import logging
import re
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from downloads._common import (
    DEFAULT_TIMEOUT,
    DEFAULT_SLEEP_SEC,
    MIN_VALID_BYTES,
    AntiBotProxy,
    AntiBotConfig,
    setup_logger,
    resolve_out_dir,
    is_valid_file,
    build_default_session,
    RunStats,
    add_exchange_suffix,
    load_classification_indices,
    load_classification_index_names,
)
from downloads._common.monthly import is_month_start

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLOSEWEIGHT_URL_TEMPLATE = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
    "file/autofile/closeweight/{index_code}closeweight.xls"
)

# Bilingual column name → normalized name mapping.
# The xls header row is bilingual (Chinese + English concatenated, no separator).
# We match by substring to be robust against minor header variations.
COLUMN_MATCHERS: List[tuple[str, str]] = [
    ("日期",              "snapshot_date_raw"),
    ("指数代码",          "index_code"),
    ("指数名称",          "index_name"),
    ("成份券代码",        "stock_code_raw"),
    ("成份券名称",        "stock_name"),
    ("交易所",            "exchange_raw"),
    ("权重",              "weight_pct"),
]

SLEEP_SEC = DEFAULT_SLEEP_SEC

# SZSE indices that must NOT be downloaded from csindex.com.cn
# (they are covered by SZSE-specific downloaders)
CSINDEX_SKIP_CODES = {"399001", "399006", "399348", "399346"}

# Bond market indices track bonds (not stocks), so they don't have meaningful
# composition (closeweight) data. They are tracked via daily index OHLCV
# data instead (downloaded by download_csindex.py).
DEBT_SECTOR_INDUSTRY_IDS = frozenset({"DEBT_TREASURY", "DEBT_CORP"})
DEBT_SECTOR_ID = "DEBT"

logger = setup_logger("csi_index_composition")


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------

def fetch_closeweight_xls(
    session: requests.Session,
    index_code: str,
    proxy: Optional[AntiBotProxy] = None,
) -> Optional[bytes]:
    """Download the closeweight xls for the given index code.

    Returns raw bytes on success, None on failure.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=DEFAULT_SLEEP_SEC))
    
    url = CLOSEWEIGHT_URL_TEMPLATE.format(index_code=index_code)

    resp = proxy.get(
        session,
        url,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=f"[dl {index_code}]",
    )
    if resp is None:
        return None
    if len(resp.content) < MIN_VALID_BYTES:
        logger.warning("[dl %s] content too small (%d bytes)", index_code, len(resp.content))
        return None
    return resp.content


# ---------------------------------------------------------------------------
# Parse xls → DataFrame
# ---------------------------------------------------------------------------

def parse_closeweight_xls(raw: bytes) -> Optional[pd.DataFrame]:
    """Parse the closeweight xls bytes into a DataFrame.

    Tries pd.read_excel (xlrd) first; falls back to pd.read_html for files
    that are actually HTML tables disguised with a .xls extension.
    """
    bio = BytesIO(raw)
    try:
        df = pd.read_excel(bio, engine="xlrd")
    except Exception:
        bio.seek(0)
        try:
            tables = pd.read_html(bio)
            if tables:
                df = tables[0]
            else:
                logger.warning("[parse] read_html returned no tables")
                return None
        except Exception as e:
            logger.warning("[parse] failed to parse xls (not Excel, not HTML): %s", e)
            return None

    if df is None or len(df) == 0:
        return None
    return df


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename bilingual columns to normalized names via substring matching."""
    rename_map: Dict[str, str] = {}
    for col in df.columns:
        col_str = str(col)
        for pattern, target in COLUMN_MATCHERS:
            if pattern in col_str and target not in rename_map.values():
                rename_map[col] = target
                break
    df = df.rename(columns=rename_map)

    # Keep only the columns we care about
    keep = [t for _, t in COLUMN_MATCHERS if t in df.columns]
    return df[keep].copy()


def _extract_snapshot_date(df: pd.DataFrame) -> Optional[str]:
    """Extract the snapshot date from the snapshot_date_raw column (YYYYMMDD)."""
    if "snapshot_date_raw" not in df.columns or len(df) == 0:
        return None
    raw_val = str(df["snapshot_date_raw"].iloc[0]).strip()
    # The date is typically a string like "20260630" or a number 20260630
    m = re.search(r"(\d{8})", raw_val)
    if not m:
        return None
    try:
        d = datetime.strptime(m.group(1), "%Y%m%d")
        return d.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _normalize_stock_code(row: pd.Series) -> str:
    """Add exchange suffix to the bare stock code based on the exchange column."""
    code = str(row.get("stock_code_raw", "")).strip()
    # Strip any existing suffix
    if "." in code:
        code = code.split(".")[0]
    # Zero-pad to 6 digits
    if code.isdigit():
        code = code.zfill(6)
    exchange = str(row.get("exchange_raw", "")).strip()
    return add_exchange_suffix(code, market=exchange)


def normalize_closeweight_df(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Normalize the parsed DataFrame into the final CSV schema.

    Returns a DataFrame with columns:
      snapshot_date, index_code, index_name, stock_code, stock_name, weight_pct
    """
    df = _normalize_columns(df)
    if df.empty:
        return None

    # Extract snapshot date (same for all rows in the file)
    snapshot_date = _extract_snapshot_date(df)
    if not snapshot_date:
        logger.warning("[parse] could not extract snapshot date from file")
        return None

    # Fill index_code/index_name from the first row if present
    index_code = ""
    index_name = ""
    if "index_code" in df.columns and len(df):
        index_code = str(df["index_code"].iloc[0]).strip()
    if "index_name" in df.columns and len(df):
        index_name = str(df["index_name"].iloc[0]).strip()

    # Build stock_code with exchange suffix
    df["stock_code"] = df.apply(_normalize_stock_code, axis=1)

    # Normalize weight_pct
    df["weight_pct"] = pd.to_numeric(df.get("weight_pct"), errors="coerce").fillna(0.0)

    # Fill missing stock_name
    if "stock_name" not in df.columns:
        df["stock_name"] = ""
    df["stock_name"] = df["stock_name"].astype(str).str.strip()

    # Filter out rows with invalid stock codes (must be 6-digit + .SS/.SZ suffix)
    df = df[df["stock_code"].str.match(r"^\d{6}\.(?:SZ|SS)$", na=False)].copy()
    if df.empty:
        logger.warning("[parse] no valid stock codes after normalization")
        return None

    # Add constant columns
    df["snapshot_date"] = snapshot_date
    df["index_code"] = index_code
    df["index_name"] = index_name

    # Final column order
    result = df[["snapshot_date", "index_code", "index_name",
                 "stock_code", "stock_name", "weight_pct"]].copy()
    result = result.sort_values("weight_pct", ascending=False).reset_index(drop=True)
    return result


# ---------------------------------------------------------------------------
# CSV save / load
# ---------------------------------------------------------------------------

def csv_filename_for(index_code: str, snapshot_date: str) -> str:
    """Build the output CSV filename: {code}_closeweight_{YYYYMMDD}.csv"""
    ymd = snapshot_date.replace("-", "")
    return f"{index_code}_closeweight_{ymd}.csv"


def find_cached_csv(out_dir: Path, index_code: str) -> Optional[Path]:
    """Find the most recent cached CSV for the given index code."""
    pattern = f"{index_code}_closeweight_*.csv"
    files = sorted(out_dir.glob(pattern))
    for f in reversed(files):
        if is_valid_file(f, min_bytes=MIN_VALID_BYTES):
            return f
    return None


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def download_index_composition(
    *,
    index_codes: Optional[List[str]] = None,
    out_root: Optional[str] = None,
    skip_cached: bool = True,
    sleep_sec: float = SLEEP_SEC,
    force_month_start: bool = False,
) -> dict:
    """Download CSI index composition (closeweight) for the given index codes.

    Args:
        index_codes: list of 6-digit index codes (default: ICONIC_INDEXES keys)
        out_root: alternative output root directory
        skip_cached: skip indices that already have a cached CSV
        sleep_sec: sleep between downloads
        force_month_start: force month-start behavior (bypass cache + stamp
            CSVs with today's date) regardless of the actual calendar day.
            Useful for testing the monthly refresh flow on any day.

    Returns:
        Summary dict with download stats and output directory.
    """
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "csi_index_composition", out_root)

    if not index_codes:
        index_codes = sorted(load_classification_index_names().keys())

    if not index_codes:
        logger.warning("No index codes to download (sec_classification.json has no indices)")
        return {"out_dir": str(out_dir), "downloaded": 0, "skipped_cached": 0, "failed": 0}

    # Load index metadata from sec_classification.json (replaces _classification.py).
    _index_meta = load_classification_indices()
    _index_names = {c: info.get("name", c) for c, info in _index_meta.items()}

    # Month-start trigger: on the 1st of each month (or when forced), stamp
    # CSVs with today's date so a fresh monthly snapshot flows to prod even
    # if the xls still reports the previous business day.  Indices that
    # already have today's CSV are skipped.
    today = date.today()
    month_start = force_month_start or is_month_start(today)
    if month_start:
        logger.info(
            "Month-start refresh (today=%s, forced=%s): stamping CSVs with "
            "today's date (overrides xls snapshot_date). Already-downloaded "
            "CSVs for today are skipped.",
            today.isoformat(), force_month_start,
        )

    logger.info(
        "Starting CSI index composition download: %d indices, out=%s",
        len(index_codes), out_dir,
    )

    session = build_default_session()
    stats = RunStats()
    
    # Create unified AntiBotProxy with jitter
    proxy_config = AntiBotConfig(
        base_sleep_sec=sleep_sec,
        sleep_jitter=0.3,
    )
    proxy = AntiBotProxy(proxy_config)
    
    results: List[Dict[str, Any]] = []

    try:
        for code in index_codes:
            code = str(code).strip()
            if not code:
                continue

            name = _index_names.get(code, code)

            if code in CSINDEX_SKIP_CODES:
                logger.info("== Index %s (%s) — skipped (in CSINDEX_SKIP_CODES, handled by SZSE downloader) ==", code, name)
                stats.skipped_cached += 1
                results.append({
                    "code": code, "name": name,
                    "status": "szse_index_skip",
                })
                continue

            logger.info("== Index %s (%s) ==", code, name)

            # Debt sector indices (国债指数, 企债指数) track bonds, not stocks,
            # so they don't have meaningful composition data. Skip them —
            # daily index OHLCV data (downloaded by download_csindex.py) is
            # sufficient.  Sector/industry are read from sec_classification.json.
            idx_info = _index_meta.get(code, {})
            sid = idx_info.get("sector_id")
            iid = idx_info.get("industry_id")
            ilabel = idx_info.get("name", code)
            if sid == DEBT_SECTOR_ID or iid in DEBT_SECTOR_INDUSTRY_IDS:
                logger.info("  [debt] %s is a debt sector index (%s), skipping "
                            "composition download (daily index only)", code, ilabel)
                stats.skipped_cached += 1
                results.append({
                    "code": code, "name": name,
                    "status": "debt_index_skip",
                    "sector": sid,
                    "industry": iid,
                })
                continue

            # Check cache.
            # On month-start: skip only if today's CSV already exists (prevents
            #   re-downloading on the same day while still creating a fresh
            #   monthly snapshot for indices not yet downloaded today).
            # On non-month-start: skip if any valid cached CSV exists.
            if month_start:
                today_csv = out_dir / f"{code}_closeweight_{today.strftime('%Y%m%d')}.csv"
                if is_valid_file(today_csv, min_bytes=MIN_VALID_BYTES):
                    logger.info("  [cache] %s already downloaded today: %s", code, today_csv.name)
                    stats.skipped_cached += 1
                    results.append({"code": code, "name": name, "status": "cached", "file": str(today_csv)})
                    continue
            else:
                cached = find_cached_csv(out_dir, code)
                if skip_cached and cached:
                    logger.info("  [cache] %s already cached: %s", code, cached.name)
                    stats.skipped_cached += 1
                    results.append({"code": code, "name": name, "status": "cached", "file": str(cached)})
                    continue

            if proxy.is_blocked(CLOSEWEIGHT_URL_TEMPLATE):
                logger.warning("  [host-blocked] OSS bucket blocked, skipping %s", code)
                stats.failed += 1
                results.append({"code": code, "name": name, "status": "blocked"})
                continue

            # Download
            raw = fetch_closeweight_xls(session, code, proxy)
            if raw is None:
                stats.failed += 1
                results.append({"code": code, "name": name, "status": "download_failed"})
                continue

            # Parse
            df = parse_closeweight_xls(raw)
            if df is None:
                stats.failed += 1
                results.append({"code": code, "name": name, "status": "parse_failed"})
                continue

            normalized = normalize_closeweight_df(df)
            if normalized is None or len(normalized) == 0:
                stats.empty += 1
                results.append({"code": code, "name": name, "status": "empty"})
                continue

            # Save CSV. On month-start refresh, stamp the CSV with today's
            # date (overriding the xls's snapshot_date, which is usually the
            # previous business day) so a fresh monthly snapshot flows to
            # prod (stats.sec_composition) under the new month's date.
            if month_start:
                snapshot_date = today.isoformat()
                normalized["snapshot_date"] = snapshot_date
            else:
                snapshot_date = normalized["snapshot_date"].iloc[0]
            csv_name = csv_filename_for(code, snapshot_date)
            csv_path = out_dir / csv_name
            normalized.to_csv(csv_path, index=False, encoding="utf-8-sig")

            stats.downloaded += 1
            stats.files.append(str(csv_path))
            results.append({
                "code": code,
                "name": name,
                "status": "downloaded",
                "file": str(csv_path),
                "n_constituents": len(normalized),
                "snapshot_date": snapshot_date,
                "month_start": month_start,
            })
            logger.info("  [ok] %s: %d constituents, snapshot=%s → %s%s",
                        code, len(normalized), snapshot_date, csv_name,
                        " (month-start stamp)" if month_start else "")

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = stats.to_dict(
        out_dir=str(out_dir),
        n_indices=len(index_codes),
        results=results,
    )
    logger.info(
        "Done CSI index composition. downloaded=%d skipped_cached=%d failed=%d empty=%d",
        stats.downloaded, stats.skipped_cached, stats.failed, stats.empty,
    )
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download CSI index composition (closeweight) xls and convert to CSV."
    )
    ap.add_argument(
        "--index-codes", type=str, default=None,
        help="Comma-separated list of index codes (default: all from sec_classification.json). "
             "Example: --index-codes 930606,000300,399997",
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
             "today's date (overrides xls snapshot_date). For testing the "
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

    result = download_index_composition(
        index_codes=codes,
        out_root=args.out_root,
        skip_cached=not args.no_skip_cached,
        sleep_sec=args.sleep_sec,
        force_month_start=args.force_month_start,
    )
    print(result)
