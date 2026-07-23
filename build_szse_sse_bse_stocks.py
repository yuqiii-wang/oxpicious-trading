"""
build_szse_sse_bse_stocks.py — Build a combined SZSE + SSE + BSE individual-stock CSV (close + PE)
from the locally cached archive + trend files.

The SZSE/SSE/BSE daily market files (download_szse_archive.py / download_szse_trend.py /
download_sse_price.py / download_bse_price.py with security_type="stock") already contain
per-stock OHLCV and 市盈率 (PE) for every Shenzhen/Shanghai/Beijing-listed stock from
2022-01-01 onward. This script consolidates those per-day CSVs into one long DataFrame
for downstream use.

CRITICAL: Stock codes must be disambiguated with exchange suffixes (.SS for Shanghai,
.SZ for Shenzhen, .BJ for Beijing) because 000xxx/001xxx codes overlap:
  - SSE (Shanghai): 000xxx codes are INDICES (e.g., 000001 = SSE Composite)
  - SZSE (Shenzhen): 000xxx codes are individual stocks (e.g., 000001 = Ping An Bank)
  - BSE (Beijing): 43xxxx / 83xxxx / 87xxxx / 920xxx codes are individual stocks

Notes:
  - 证券代码 is stored without leading zeros in the source CSV ("1" → "000001");
    we zero-pad to 6 digits to match composition stock_code.
  - Volume/amount use comma thousands separators; read with thousands=','.
  - Holiday files contain a single "没有找到符合条件的数据！" placeholder row;
    filtered out by requiring a numeric stock code.
  - 市盈率 == 0 marks loss-making stocks (SZSE convention); kept as-is and
    excluded downstream when computing weighted PE.
  - SSE price endpoint does not publish PE data; those rows have empty PE.

Output:
  analysis_output/szse_sse_stock/stock_combined.csv
    columns: date, code, name, prev_close, open, high, low, close, pct_change, pe

  Mirrors etf_basic_stats (date, code, prev_close, open, high, low, close,
  pct_change) + stock-specific pe. DB tables stock_identity / stock_basic_stats
  use the same column names as etf_identity / etf_basic_stats for symmetry.

Usage:
  python build_szse_sse_bse_stocks.py
  python build_szse_sse_bse_stocks.py --limit 50     # dev: first 50 files only
"""
import os
import sys
import glob
import time
import argparse
import datetime
from collections import Counter

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from _download_commons import add_exchange_suffix
from _db_commons import (
    get_db_connection_async, get_existing_keys_async, bulk_upsert_async,
    truncate_table_async
)

# ---------------------------------------------------------------------------
# stdout encoding (Windows)
# ---------------------------------------------------------------------------
import locale as _locale
try:
    _locale.setlocale(_locale.LC_ALL, "")
except Exception:
    pass
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ============================================================================
# Paths
# ============================================================================
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
TEMP_DATA    = os.path.join(PROJECT_ROOT, "temp_data")
SZSE_ARCHIVE_DIR  = os.path.join(PROJECT_ROOT, "temps", "szse_archive")
SZSE_TREND_DIR    = os.path.join(PROJECT_ROOT, "temps", "szse_trend")
SSE_TREND_DIR     = os.path.join(PROJECT_ROOT, "temps", "sse_trend")
BSE_TREND_DIR     = os.path.join(PROJECT_ROOT, "temps", "bse_trend")
OUTPUT_DIR        = os.path.join(TEMP_DATA, "analysis_output", "szse_sse_stock")
os.makedirs(OUTPUT_DIR, exist_ok=True)

TODAY_STR = datetime.datetime.now().strftime("%Y-%m-%d")

OUT_PATH = os.path.join(OUTPUT_DIR, "stock_combined.csv")

COL_MAP = {
    "交易日期":     "date",
    "证券代码":     "code",
    "证券简称":     "name",
    "前收":         "prev_close",
    "开盘":         "open",
    "最高":         "high",
    "最低":         "low",
    "今收":         "close",
    "涨跌幅（%）":  "pct_change",
    "市盈率":       "pe",
}


def _read_one(path, market):
    """Read one stock CSV, return a lean DataFrame or None (holiday/empty).

    Args:
        path: path to the CSV file
        market: "深圳" for SZSE, "上海" for SSE, "北京" for BSE — used to add exchange suffix
    """
    try:
        df = pd.read_csv(path, dtype={"证券代码": str}, thousands=",")
    except Exception:
        return None
    if "证券代码" not in df.columns:
        return None
    df = df[df["证券代码"].notna()].copy()
    df["证券代码"] = df["证券代码"].astype(str).str.strip()
    # Accept codes with or without exchange suffix (e.g. "000001" or "000001.SZ").
    # The original regex `^\d{1,6}(\.0+)?$` only matched float-conversion artifacts
    # (e.g. "1.0") and rejected every real row because source CSVs now store codes
    # with the suffix already appended.
    df = df[df["证券代码"].str.match(r"^\d{1,6}(\.(?:SZ|SS|SH|BJ|sz|ss|sh|bj))?$", na=False)].copy()
    if df.empty:
        return None
    keep = {k: v for k, v in COL_MAP.items() if k in df.columns}
    out = df[list(keep.keys())].rename(columns=keep).copy()
    out["code"] = out["code"].apply(
        lambda c: str(c).split(".")[0].zfill(6)
    ).apply(lambda c: add_exchange_suffix(c, market))
    for c in ("prev_close", "open", "high", "low", "close", "pct_change", "pe"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def build_all(limit=None, verbose=True):
    szse_archive_files = sorted(glob.glob(os.path.join(SZSE_ARCHIVE_DIR, "szse_stock_*.csv")))
    szse_trend_files   = sorted(glob.glob(os.path.join(SZSE_TREND_DIR, "szse_trend_stock_*.csv")))
    sse_trend_files    = sorted(glob.glob(os.path.join(SSE_TREND_DIR, "sse_trend_stock_*.csv")))
    bse_trend_files    = sorted(glob.glob(os.path.join(BSE_TREND_DIR, "bse_trend_stock_*.csv")))

    files = szse_archive_files + szse_trend_files + sse_trend_files + bse_trend_files
    if limit:
        files = files[:limit]

    if verbose:
        print(f"  [SCAN] {len(szse_archive_files)} szse_archive + "
              f"{len(szse_trend_files)} szse_trend + "
              f"{len(sse_trend_files)} sse_trend + "
              f"{len(bse_trend_files)} bse_trend "
              f"= {len(files)} stock CSVs", flush=True)

    counts = Counter()
    frames = []
    for path in files:
        low_path = path.lower()
        if "bse" in low_path:
            market = "北京"
        elif "szse" in low_path:
            market = "深圳"
        else:
            market = "上海"
        df = _read_one(path, market)
        if df is None or df.empty:
            counts["empty"] += 1
            continue
        counts["ok"] += 1
        counts["rows"] += len(df)
        frames.append(df)

    if not frames:
        print("    [FATAL] No stock rows parsed from any CSV", flush=True)
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
    combined = combined.dropna(subset=["date"])
    combined = combined.sort_values(["date", "code"]).reset_index(drop=True)
    combined = combined.drop_duplicates(
        subset=["date", "code"], keep="last").reset_index(drop=True)

    combined.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    if verbose:
        n_dates = combined["date"].dt.strftime("%Y-%m-%d").nunique()
        n_stocks = combined["code"].nunique()
        d0 = combined["date"].min().strftime("%Y-%m-%d")
        d1 = combined["date"].max().strftime("%Y-%m-%d")
        n_szse = combined["code"].str.endswith(".SZ").sum()
        n_sse = combined["code"].str.endswith(".SS").sum()
        n_bse = combined["code"].str.endswith(".BJ").sum()
        print(f"    [SAVE] {OUT_PATH}", flush=True)
        print(f"           {len(combined):,} rows | {n_stocks} stocks | "
              f"{n_dates} dates | {d0} → {d1}", flush=True)
        print(f"           SZSE (.SZ): {n_szse:,} | SSE (.SS): {n_sse:,} | BSE (.BJ): {n_bse:,}", flush=True)
        print(f"           pe non-null: {combined['pe'].notna().sum():,} | "
              f"pe>0: {(combined['pe'] > 0).sum():,}", flush=True)

    print(f"\n  [STATS] ok={counts['ok']} empty={counts['empty']} "
          f"total_rows={counts['rows']:,}", flush=True)
    return combined


async def main():
    import asyncio
    ap = argparse.ArgumentParser(description="Build combined SZSE + SSE + BSE stock CSV (close+PE) and insert to database.")
    ap.add_argument("--limit", type=int, default=None, help="Dev: first N files only")
    ap.add_argument("--force", action="store_true", help="Rebuild all data (truncate tables first)")
    args = ap.parse_args()

    t0 = time.time()
    print("=" * 78, flush=True)
    print("  SZSE + SSE + BSE STOCK BUILDER  ·  per-day CSV → stock_combined.csv + DATABASE", flush=True)
    print("=" * 78, flush=True)
    print(f"  SZSE Archive dir: {SZSE_ARCHIVE_DIR}", flush=True)
    print(f"  SSE Trend dir   : {SSE_TREND_DIR}", flush=True)
    print(f"  BSE Trend dir   : {BSE_TREND_DIR}", flush=True)
    print(f"  Output          : {OUT_PATH}", flush=True)
    print(f"  Today           : {TODAY_STR}", flush=True)

    combined = build_all(limit=args.limit, verbose=True)

    if len(combined) > 0:
        # ------------------------------------------------------------------
        # Insert to database
        # ------------------------------------------------------------------
        print("\n[DB] Inserting data to database …", flush=True)

        # Connect to database (async)
        print("    [DB] Connecting to database …", flush=True)
        try:
            conn = await get_db_connection_async()
            print("    [DB] Connected successfully", flush=True)
        except Exception as e:
            print(f"    [DB] [WARN] Database connection failed: {e}", flush=True)
            print(f"    [DB] Continuing without database insertion", flush=True)
        else:
            try:
                if args.force:
                    print("    [DB] Force mode: truncating existing tables", flush=True)
                    await truncate_table_async(conn, "stats.stock_basic_stats")
                    await truncate_table_async(conn, "stats.stock_identity")

                # Convert date to datetime.date for asyncpg DATE codec.
                # asyncpg requires datetime.date instances; passing str raises
                # "expected a date instance, got 'str'".
                combined_db = combined.copy()
                combined_db["date"] = combined_db["date"].dt.date

                # Get existing keys from stock_identity (the PK table)
                existing_keys = await get_existing_keys_async(
                    conn, "stats.stock_identity", ["date", "code"]
                )
                print(f"    [DB] {len(existing_keys):,} existing (date, code) pairs in stats.stock_identity", flush=True)

                # Build rows for stock_identity and stock_basic_stats,
                # skipping any whose (date, code) already exist.
                identity_rows = []
                basic_stats_rows = []
                for _, row in combined_db.iterrows():
                    key = (row["date"], row["code"])
                    if key in existing_keys:
                        continue
                    identity_rows.append({
                        "date": row["date"],
                        "code": row["code"],
                        "code_suffix": row["code"].split(".")[-1] if "." in str(row["code"]) and str(row["code"]).split(".")[-1] in ("SZ", "SS", "BJ") else None,
                        "name": str(row.get("name", "")) if pd.notna(row.get("name")) else "",
                    })
                    basic_stats_rows.append({
                        "date": row["date"],
                        "code": row["code"],
                        "prev_close": row.get("prev_close"),
                        "open": row.get("open"),
                        "high": row.get("high"),
                        "low": row.get("low"),
                        "close": row.get("close"),
                        "pct_change": row.get("pct_change"),
                        "pe": row.get("pe"),
                    })

                # Bulk upsert stock_identity first (FK parent), then stock_basic_stats
                if identity_rows:
                    inserted = await bulk_upsert_async(
                        conn, "stats.stock_identity", identity_rows, ["date", "code"]
                    )
                    print(f"    [DB] Inserted {inserted:,} rows into stats.stock_identity", flush=True)
                else:
                    print(f"    [DB] No new rows to insert into stats.stock_identity", flush=True)

                if basic_stats_rows:
                    inserted = await bulk_upsert_async(
                        conn, "stats.stock_basic_stats", basic_stats_rows, ["date", "code"]
                    )
                    print(f"    [DB] Inserted {inserted:,} rows into stats.stock_basic_stats", flush=True)
                else:
                    print(f"    [DB] No new rows to insert into stats.stock_basic_stats", flush=True)
            finally:
                await conn.close()

    print(f"\n  Wall time: {int(time.time()-t0)}s", flush=True)
    print("=" * 78, flush=True)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
