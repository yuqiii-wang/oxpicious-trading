"""Build SSE + SZSE stock dividend (利润分配/分红) data from per-stock CSV files
into ``stats.stock_dividends``.

Source CSVs are produced by two downloaders, both writing one file per stock
per download date as ``{code}_dividend_{YYYYMMDD}.csv`` (legacy files without
a date suffix are also supported):

  • SSE  — ``downloads.stock.sse.dividend``  → ``temps/sse_archive/``
            (SSE commonQuery.do, sqlId COMMON_SSE_CP_GPJCTPZ_GPLB_LRFP_FH_L)
  • SZSE — ``downloads.stock.szse.dividend`` → ``temps/szse_archive/``
            (cninfo webapi.cninfo.com.cn p_sysapi1139 分红转增信息)

When multiple CSVs exist for the same code (from different download dates),
this script loads only the LATEST one (highest date suffix). Legacy files
(``{code}_dividend.csv`` without a date) are used only when no dated file
exists for that code.

Rows from BOTH directories are bulk-upserted into ``stats.stock_dividends``.
SSE rows get ``source='SSE'``; SZSE rows get ``source='SZSE'``.

CSV schema (shared by both sources — cninfo leaves the columns it does not
provide empty, so they are stored as NULL):
    公告日期, 证券代码, 证券简称, 股权登记日, 除息交易日,
    每股红利(含税)(元), 每股红利(税后)(元), 分红总额(万元),
    除息前日收盘价, 除息报价, 总股本(万股)

Notes:
  • ``公告日期`` is left blank by the SSE 分红 API — stored as NULL. The
    SZSE (cninfo) API DOES populate it (F018D 实施方案公告日期).
  • Stock code is suffixed with ``.SS`` (Shanghai) or ``.SZ`` (Shenzhen)
    to match ``stats.stock_identity.code``. Bare 6-digit codes in the CSV
    are suffixed before insert.
  • PK (code, ex_dividend_date) means upsert is idempotent — re-running
    the script on the same data is safe.
  • Empty dividend CSVs (stocks with no dividend history) are silently
    skipped — they don't contribute any rows to the DB.
  • In all-stocks mode, only stocks whose ``stats.sec_classification``
    row has ``is_active=TRUE`` (>=1 record in ``stock_identity`` within
    the trailing 365 days) are loaded. Delisted / dead stocks are
    skipped. Single-stock ``--code`` mode bypasses this filter.

Usage:
  python -m builds.stock.dividends                    # all SSE + SZSE stocks
  python -m builds.stock.dividends --code 600008      # single SSE stock
  python -m builds.stock.dividends --code 000651      # single SZSE stock
  python -m builds.stock.dividends --force            # truncate first
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()
import argparse
import asyncio
import csv
import datetime
import glob
import os
import re
import sys
import time
from operator import itemgetter
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from _common.build_commons import (
    setup_utf8_stdout, get_db_or_exit,
    bulk_upsert_async, truncate_table_async,
    print_build_header, print_wall_time, PROJECT_ROOT,
)
from _common.db_commons import get_db_connection

setup_utf8_stdout()

# ============================================================================
# Paths + constants
# ============================================================================
SSE_ARCHIVE_DIR = os.path.join(PROJECT_ROOT, "temps", "sse_archive")
SZSE_ARCHIVE_DIR = os.path.join(PROJECT_ROOT, "temps", "szse_archive")

# CSV column → DB column map. The CSV uses Chinese headers (mirroring the
# SSE API); the DB uses English snake_case names.
CSV_TO_DB = {
    "公告日期":         "announcement_date",
    "证券代码":         "code_raw",
    "证券简称":         "name",
    "股权登记日":       "record_date",
    "除息交易日":       "ex_dividend_date",
    "每股红利(含税)(元)": "dividend_per_share_pre_tax",
    "每股红利(税后)(元)": "dividend_per_share_post_tax",
    "分红总额(万元)":   "total_dividend_wan",
    "除息前日收盘价":   "pre_close_price",
    "除息报价":         "open_price",
    "总股本(万股)":     "total_shares_wan",
}

# DB columns in the order they appear in bulk_upsert rows. `code` is added
# later (with .SS / .SZ suffix); `source` is set per-row ('SSE' or 'SZSE');
# `last_updated` has a DB default (NOW()).
DB_COLUMNS = [
    "code",
    "name",
    "announcement_date",
    "record_date",
    "ex_dividend_date",
    "dividend_per_share_pre_tax",
    "dividend_per_share_post_tax",
    "total_dividend_wan",
    "pre_close_price",
    "open_price",
    "total_shares_wan",
    "source",
]


# ============================================================================
# CSV reader
# ============================================================================
def _to_date(val: str) -> Optional[datetime.date]:
    """Parse 'YYYY-MM-DD' → datetime.date. Returns None for empty/invalid."""
    if not val:
        return None
    s = str(val).strip()
    if not s or s in ("-", "--", "null", "NULL"):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _to_num(val: str) -> Optional[float]:
    """Parse a numeric CSV cell to float. Returns None for empty/invalid.

    The SSE API returns numeric fields as strings; '-' / '' markers are
    treated as NULL.
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s or s in ("-", "--", "null", "NULL", "None", "nan", "NaN"):
        return None
    s = s.replace(",", "").replace("，", "").replace(" ", "")
    try:
        v = float(s)
        return v if np.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def _read_dividend_csv(path: str, exchange: str, source: str) -> List[Dict[str, Any]]:
    """Read one {code}_dividend.csv and return a list of DB-row dicts.

    The CSV's bare 6-digit code is suffixed with *exchange* (``SS`` for
    Shanghai, ``SZ`` for Shenzhen) before insert, matching the format used
    in ``stats.stock_identity.code``. *source* is set on every row
    (``'SSE'`` or ``'SZSE'``) so the DB's ``source`` column reflects the
    data origin.
    """
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                # Skip empty rows (the SSE API occasionally returns blank
                # rows for stocks with no dividend history).
                if not raw.get("除息交易日"):
                    continue
                ex_date = _to_date(raw.get("除息交易日", ""))
                if ex_date is None:
                    continue  # malformed row, skip
                code_raw = str(raw.get("证券代码", "")).strip()
                if not code_raw or not code_raw.isdigit():
                    continue
                code = f"{code_raw.zfill(6)}.{exchange}"
                row = {
                    "code": code,
                    "name": (raw.get("证券简称") or "").strip() or None,
                    "announcement_date": _to_date(raw.get("公告日期", "")),
                    "record_date": _to_date(raw.get("股权登记日", "")),
                    "ex_dividend_date": ex_date,
                    "dividend_per_share_pre_tax":
                        _to_num(raw.get("每股红利(含税)(元)", "")),
                    "dividend_per_share_post_tax":
                        _to_num(raw.get("每股红利(税后)(元)", "")),
                    "total_dividend_wan":
                        _to_num(raw.get("分红总额(万元)", "")),
                    "pre_close_price":
                        _to_num(raw.get("除息前日收盘价", "")),
                    "open_price": _to_num(raw.get("除息报价", "")),
                    "total_shares_wan":
                        _to_num(raw.get("总股本(万股)", "")),
                    "source": source,
                }
                rows.append(row)
    except (OSError, csv.Error) as e:
        print(f"    [WARN] Failed to read {path}: {e}", flush=True)
    return rows


# Each source directory paired with its exchange and source label.
# SSE codes:  600xxx/601xxx/603xxx/605xxx/688xxx → SS
# SZSE codes: 000xxx/001xxx/002xxx/003xxx/300xxx/301xxx → SZ
SOURCE_DIRS = [
    (SSE_ARCHIVE_DIR,  "SS", "SSE"),
    (SZSE_ARCHIVE_DIR, "SZ", "SZSE"),
]

# SSE-exclusive prefixes (6xx). Everything else found in the SZSE dir is
# treated as SZSE. Used to resolve --code to the right directory.
_SSE_CODE_PREFIXES = ("600", "601", "603", "605", "688")


def _resolve_code_source(bare_code: str) -> Tuple[str, str, str]:
    """Return (archive_dir, exchange, source) for a bare 6-digit code.

    SSE prefixes (600/601/603/605/688) → SSE_ARCHIVE_DIR, 'SS', 'SSE'.
    All others → SZSE_ARCHIVE_DIR, 'SZ', 'SZSE'.
    """
    if bare_code[:3] in _SSE_CODE_PREFIXES:
        return (SSE_ARCHIVE_DIR, "SS", "SSE")
    return (SZSE_ARCHIVE_DIR, "SZ", "SZSE")


# Regex patterns for dividend CSV filenames.
#   New:  {code}_dividend_{YYYYMMDD}.csv  — date suffix from download date
#   Legacy: {code}_dividend.csv           — pre-date-suffix convention
_RE_DATED = re.compile(r'^(\d{6})_dividend_(\d{8})\.csv$')
_RE_LEGACY = re.compile(r'^(\d{6})_dividend\.csv$')


def _extract_code_date(path: str) -> Optional[Tuple[str, str]]:
    """Extract (bare_code, sort_key) from a dividend CSV path.

    ``sort_key`` is the 8-digit date suffix for new-format files, or '0' for
    legacy files (so any dated file outranks a legacy file). Returns None if
    the filename doesn't match either dividend CSV pattern.
    """
    base = os.path.basename(path)
    m = _RE_DATED.match(base)
    if m:
        return (m.group(1), m.group(2))
    m = _RE_LEGACY.match(base)
    if m:
        return (m.group(1), "0")
    return None


def _find_latest_dividend_csvs(archive_dir: str) -> Dict[str, str]:
    """Find the latest dividend CSV per stock code in ``archive_dir``.

    Globs both ``*_dividend_*.csv`` (new dated format) and ``*_dividend.csv``
    (legacy). For each stock code, returns the file with the highest date
    suffix. Legacy files (no date) rank below any dated file.

    Returns ``{bare_code: path_to_latest_csv}``.
    """
    latest: Dict[str, Tuple[str, str]] = {}  # code → (sort_key, path)
    # Glob both patterns — they're mutually exclusive (a file ending in
    # _dividend.csv cannot also end in _dividend_YYYYMMDD.csv).
    for pattern in ("*_dividend_*.csv", "*_dividend.csv"):
        for p in glob.glob(os.path.join(archive_dir, pattern)):
            result = _extract_code_date(p)
            if result is None:
                continue
            code, sort_key = result
            if code not in latest or sort_key > latest[code][0]:
                latest[code] = (sort_key, p)
    return {code: path for code, (_, path) in latest.items()}


def _find_latest_for_code(archive_dir: str, bare_code: str) -> Optional[str]:
    """Find the latest dividend CSV for a single stock code.

    Returns the path to the file with the highest date suffix, or the legacy
    file if no dated files exist. Returns None if no file is found.
    """
    latest = _find_latest_dividend_csvs(archive_dir)
    return latest.get(bare_code)


def _fetch_active_stock_codes() -> Optional[set]:
    """Return the set of active stock codes (with .SS/.SZ suffix) from
    ``stats.sec_classification`` (type='stock', is_active=TRUE), or None
    if the table has no rows (e.g. classification not yet built).

    A security is marked active iff it has >=1 record in
    ``stats.stock_identity`` within the trailing 365 days (see
    ``builds.classification.sector_industry.upsert._update_is_active``).
    Used to filter dividend CSV files so we don't read / upsert data for
    delisted stocks (whose dividend history is no longer relevant).
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT code FROM stats.sec_classification "
                "WHERE type = 'stock' AND is_active = TRUE"
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    # Whole-column extraction (positional single-column rows)
    return set(map(itemgetter(0), rows))


# ============================================================================
# Main pipeline
# ============================================================================
async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build SSE + SZSE stock dividend (利润分配/分红) data into "
                    "stats.stock_dividends. Reads {code}_dividend_{YYYYMMDD}.csv "
                    "files from temps/sse_archive/ and temps/szse_archive/. "
                    "When multiple files exist per code (different download "
                    "dates), the latest date suffix is loaded."
    )
    ap.add_argument(
        "--code", type=str, default=None,
        help="Single-stock mode: load dividend data for one stock "
             "(bare 6-digit code, e.g. 600008 for SSE, 000651 for SZSE). "
             "The exchange is auto-detected from the code prefix.",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="Truncate stats.stock_dividends before loading (full reload).",
    )
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "STOCK DIVIDENDS BUILDER  ·  SSE + SZSE  →  stats.stock_dividends",
        **{
            "SSE dir":   SSE_ARCHIVE_DIR,
            "SZSE dir":  SZSE_ARCHIVE_DIR,
            "Mode":      f"single-stock ({args.code})" if args.code else "all SSE + SZSE stocks",
            "Force":     str(args.force),
        }
    )

    # ------------------------------------------------------------------
    # 1. Discover dividend CSV files
    # ------------------------------------------------------------------
    print("\n[1/3] Discovering dividend CSV files …", flush=True)
    # Build a list of (path, exchange, source) tuples. For each stock
    # code, only the LATEST CSV (by date suffix) is loaded — older files
    # from previous download dates are ignored. Legacy files without a
    # date suffix are used only when no dated file exists for that code.
    file_specs: List[Tuple[str, str, str]] = []
    if args.code:
        bare = args.code.split(".")[0]
        archive_dir, exchange, source = _resolve_code_source(bare)
        path = _find_latest_for_code(archive_dir, bare)
        if path is None:
            print(f"    [FATAL] No dividend CSV for {bare} found in "
                  f"{archive_dir}", flush=True)
            sys.exit(1)
        file_specs.append((path, exchange, source))
    else:
        for archive_dir, exchange, source in SOURCE_DIRS:
            latest_map = _find_latest_dividend_csvs(archive_dir)
            for code in sorted(latest_map):
                file_specs.append((latest_map[code], exchange, source))
    print(f"    → {len(file_specs)} dividend CSV files found "
          f"(SSE + SZSE)", flush=True)
    if not file_specs:
        print("    [FATAL] No dividend CSV files found. Run "
              "`python -m downloads.stock.sse.dividend` and/or "
              "`python -m downloads.stock.szse.dividend` first.", flush=True)
        sys.exit(1)

    # Filter to active stocks (sec_classification.is_active=TRUE). Skip in
    # single-stock mode (explicit --code is always processed). If
    # sec_classification has no stock rows yet (classification not built),
    # skip the filter to avoid dropping all data.
    if not args.code:
        active_codes = _fetch_active_stock_codes()
        if active_codes is not None:
            filtered: List[Tuple[str, str, str]] = []
            n_dropped = 0
            for path, suffix, source in file_specs:
                result = _extract_code_date(path)
                if result is None:
                    continue
                bare_code = result[0]
                full_code = f"{bare_code}{suffix}"
                if full_code in active_codes:
                    filtered.append((path, suffix, source))
                else:
                    n_dropped += 1
            print(f"    → is_active filter: kept {len(filtered)}, "
                  f"dropped {n_dropped} (delisted / no recent identity "
                  f"records) out of {len(file_specs)}", flush=True)
            file_specs = filtered
            if not file_specs:
                print("    [INFO] No active stock dividend CSV files "
                      "to process.", flush=True)
                print_wall_time(t0)
                return
        else:
            print("    → sec_classification empty, skipping is_active "
                  "filter", flush=True)

    # ------------------------------------------------------------------
    # 2. Read all CSVs into DB rows
    # ------------------------------------------------------------------
    print("\n[2/3] Reading dividend CSVs …", flush=True)
    all_rows: List[Dict[str, Any]] = []
    n_files_with_data = 0
    n_files_empty = 0
    for path, exchange, source in file_specs:
        rows = _read_dividend_csv(path, exchange, source)
        if rows:
            all_rows.extend(rows)
            n_files_with_data += 1
        else:
            n_files_empty += 1
    print(f"    → {len(all_rows):,} dividend rows from {n_files_with_data} files "
          f"({n_files_empty} empty)", flush=True)
    if not all_rows:
        print("    [INFO] No dividend rows to insert", flush=True)
        print_wall_time(t0)
        return

    # Dedupe by (code, ex_dividend_date) — keep last in case multiple CSV
    # files reference the same stock (e.g. downloaded twice).
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for r in all_rows:
        key = (r["code"], r["ex_dividend_date"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    n_dupes = len(all_rows) - len(deduped)
    if n_dupes > 0:
        print(f"    → {n_dupes} duplicate rows removed ({len(deduped):,} unique)",
              flush=True)
        all_rows = deduped

    n_codes = len({r["code"] for r in all_rows})
    n_sse = sum(1 for r in all_rows if r["source"] == "SSE")
    n_szse = sum(1 for r in all_rows if r["source"] == "SZSE")
    d_min = min(r["ex_dividend_date"] for r in all_rows)
    d_max = max(r["ex_dividend_date"] for r in all_rows)
    print(f"    → {n_codes} unique stocks | ex-dividend dates "
          f"{d_min} → {d_max}", flush=True)
    print(f"    → SSE rows: {n_sse:,} | SZSE rows: {n_szse:,}", flush=True)

    # ------------------------------------------------------------------
    # 3. Connect to DB and upsert
    # ------------------------------------------------------------------
    print("\n[3/3] Connecting to database …", flush=True)
    conn = await get_db_or_exit()
    try:
        if args.force:
            print("    [DB] Force mode: truncating stats.stock_dividends", flush=True)
            await truncate_table_async(conn, "stats.stock_dividends")

        inserted = await bulk_upsert_async(
            conn, "stats.stock_dividends", all_rows,
            key_columns=["code", "ex_dividend_date"],
        )
        print(f"    [DB] Upserted {inserted:,} rows into stats.stock_dividends "
              f"(PK: code, ex_dividend_date — conflicts overwrite)", flush=True)
    finally:
        await conn.close()

    print_wall_time(t0)


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
