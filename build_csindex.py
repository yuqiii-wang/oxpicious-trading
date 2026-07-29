"""
build_csindex.py — Build CSIndex daily history + 5min intraday to DATABASE
(missing-data-only, no intermediate CSV).

Reads the history and intraday CSV archives produced by download_csindex.py:
  • {code}_history.csv        (daily OHLCV + PE + amount)
  • {code}_intraday_{date}.csv (intraday ticks at ~15s intervals)

Also reads SZSE index daily CSVs produced by download_szse_archive.py and
download_szse_trend.py (TABKEY=tab7 指数), filtered to two broad-market
benchmarks:
  • temps/szse_archive/szse_index_YYYYMMDD.csv       (399001, 399006)
  • temps/szse_trend/szse_trend_index_YYYYMMDD.csv    (399001, 399006)

SZSE columns (交易日期, 指数代码, ...) are mapped to the CSIndex schema
(date, indexCode, ...). Fields not provided by SZSE (volume, pe,
consNumber) are set to NULL; absolute change is computed as close − prev_close.

Resamples intraday ticks to 5-minute OHLCV bars and merges with daily data.
Computes moving averages (ma5, ma20, ma60, ma120, ma255) from daily close.

Missing-data detection flow:
  DAILY:
    1. Query stats.index_identity for existing (date, code) pairs
    2. Read each {code}_history.csv (full history — needed for MA computation)
    3. Read SZSE index CSVs (archive + trend) for codes 399001 / 399006
    4. Compute MAs over the full per-code history
    5. Filter rows to (date, code) pairs NOT in existing_keys
    6. Bulk upsert only the missing rows
  INTRADAY:
    1. Query stats.index_intraday_5min for existing (date, code) pairs
       (SELECT DISTINCT date, code — one row per (date, code) regardless of
       how many 5min bars exist)
    2. Glob {code}_intraday_{date}.csv files
    3. Filter to files whose (code, date) is NOT in existing pairs
    4. Resample only those files to 5min bars
    5. Bulk upsert into index_intraday_5min

With --force: truncate all 5 index_* tables first, so all source data is
treated as missing.

Inserts to database tables:
  • index_identity          (date, code, name)
  • index_basic_stats       (date, code, OHLCV, volume, amount, change)
  • index_valuation         (date, code, PE, consNumber)
  • index_tech_stats        (date, code, MAs)
  • index_intraday_5min     (date, code, time, OHLC, change)

Usage:
  python build_csindex.py
  python build_csindex.py --force   (rebuild all)
"""
import os, sys, re, glob, time, argparse
import datetime

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _build_commons import (
    setup_utf8_stdout, add_common_build_args, get_db_or_exit,
    find_missing_keys, parse_num, parse_date, parse_time,
    ymd_to_date, print_build_header, print_wall_time,
    PROJECT_ROOT, TODAY_STR,
    get_existing_keys_async, bulk_upsert_async, truncate_table_async,
)

setup_utf8_stdout()

import asyncio

# ============================================================================
# Paths
# ============================================================================
CSINDEX_DIR = os.path.join(PROJECT_ROOT, "temps", "csindex")
SZSE_ARCHIVE_DIR = os.path.join(PROJECT_ROOT, "temps", "szse_archive")
SZSE_TREND_DIR = os.path.join(PROJECT_ROOT, "temps", "szse_trend")

# SZSE broad-market benchmarks to load from szse_archive/szse_trend index CSVs.
# These supplement the CSIndex history files with SZSE-only indexes.
SZSE_INDEX_CODES = {"399001", "399006"}  # 深证成指, 创业板指


# ============================================================================
# Load SZSE index daily CSVs (archive + trend) → CSIndex schema
# ============================================================================
def _load_szse_index_history(verbose: bool = True) -> list:
    """Load SZSE index daily CSVs (archive + trend) and map to CSIndex schema.

    Scans two directories for per-date index CSV files:
      • temps/szse_archive/szse_index_YYYYMMDD.csv       (historical archive)
      • temps/szse_trend/szse_trend_index_YYYYMMDD.csv   (recent trend)

    Each CSV contains ~180 indexes for one date; this function keeps only
    399001 (深证成指) and 399006 (创业板指) and maps columns to the CSIndex
    history schema so they can be concatenated with CSIndex DataFrames.

    Returns a list of per-code DataFrames (one per SZSE index code), each
    with the same columns as a CSIndex *_history.csv after schema
    normalization (date, code, indexName, open, high, low, close, volume,
    amount, change, changePct, pe, consNumber). Returns an empty list
    if no files are found.
    """
    archive_files = sorted(glob.glob(os.path.join(SZSE_ARCHIVE_DIR, "szse_index_*.csv")))
    trend_files = sorted(glob.glob(os.path.join(SZSE_TREND_DIR, "szse_trend_index_*.csv")))
    all_files = archive_files + trend_files

    if verbose:
        print(f"    [SZSE] {len(archive_files)} archive + {len(trend_files)} trend "
              f"index CSVs found", flush=True)

    if not all_files:
        return []

    # Column rename map: SZSE Chinese → CSIndex schema
    RENAME = {
        "交易日期": "date",
        "指数代码": "indexCode",
        "指数简称": "indexName",
        "前收": "prev_close",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "今收": "close",
        "涨跌幅（%）": "changePct",
        "成交金额(亿元)": "amount",
    }

    dfs = []
    for path in all_files:
        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue

        df = df.rename(columns=RENAME)

        # Filter to the two indexes we want
        df["indexCode"] = df["indexCode"].astype(str).str.strip()
        df = df[df["indexCode"].isin(SZSE_INDEX_CODES)].copy()
        if len(df) == 0:
            continue

        # Parse numerics
        for col in ["prev_close", "open", "high", "low", "close", "amount", "changePct"]:
            if col in df.columns:
                df[col] = df[col].apply(parse_num)

        # Parse date
        df["date"] = df["date"].apply(parse_date)
        df = df.dropna(subset=["date"])

        # Compute absolute change = close - prev_close
        df["change"] = (df["close"] - df["prev_close"]).round(4)

        # Fields not provided by SZSE index data
        df["volume"] = None
        df["pe"] = None
        df["consNumber"] = None

        # code column (used by build_daily_df for grouping + existing_keys check)
        df["code"] = df["indexCode"]

        dfs.append(df)

    if not dfs:
        if verbose:
            print(f"    [SZSE] No valid index data loaded", flush=True)
        return []

    combined = pd.concat(dfs, ignore_index=True)

    # Deduplicate by (date, code) — trend files are appended after archive,
    # so keep="last" gives trend data priority for overlapping dates.
    combined = combined.drop_duplicates(subset=["date", "code"], keep="last")

    if verbose:
        for code in sorted(combined["code"].unique()):
            sub = combined[combined["code"] == code]
            name = sub["indexName"].iloc[0] if len(sub) else ""
            print(f"    [SZSE] {code} {name}: {len(sub)} dates "
                  f"({sub['date'].min()} → {sub['date'].max()})", flush=True)

    # Return per-code DataFrames (same structure as CSIndex history files)
    return [combined[combined["code"] == code].copy() for code in combined["code"].unique()]


# ============================================================================
# Build daily history DataFrame (full per-code history for MA correctness)
# ============================================================================
def build_daily_df(existing_keys: set, verbose: bool = True) -> pd.DataFrame:
    """Read all *_history.csv files, compute MAs, filter to missing (date, code) pairs.

    Args:
        existing_keys: set of (date, code) tuples already in stats.index_identity.
                       Rows matching these keys are skipped before insert.

    Returns a DataFrame with MA columns, filtered to missing (date, code) pairs.
    """
    history_files = sorted(glob.glob(os.path.join(CSINDEX_DIR, "*_history.csv")))
    if verbose:
        print(f"    [DAILY] {len(history_files)} history CSVs in {CSINDEX_DIR}", flush=True)

    dfs = []
    n_skipped_files = 0
    for path in history_files:
        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue

        code = os.path.basename(path).replace("_history.csv", "")
        df["code"] = code

        # Backward compat: old CSVs use "turnover", new ones use "amount"
        if "turnover" in df.columns and "amount" not in df.columns:
            df = df.rename(columns={"turnover": "amount"})

        for col in ["open", "high", "low", "close", "volume", "amount", "change", "changePct", "pe", "consNumber"]:
            if col in df.columns:
                df[col] = df[col].apply(parse_num)

        df["date"] = df["date"].apply(parse_date)
        df = df.dropna(subset=["date"])

        # Skip file entirely if ALL its (date, code) pairs are already in DB
        file_keys = {(d, code) for d in df["date"]}
        if not file_keys:
            continue
        if file_keys.issubset(existing_keys):
            n_skipped_files += 1
            continue

        dfs.append(df)

    # Also load SZSE index data (archive + trend) for 399001 / 399006
    szse_dfs = _load_szse_index_history(verbose=verbose)
    for df in szse_dfs:
        code = df["code"].iloc[0]
        # Skip if ALL its (date, code) pairs are already in DB
        file_keys = {(d, code) for d in df["date"]}
        if not file_keys:
            continue
        if file_keys.issubset(existing_keys):
            n_skipped_files += 1
            continue
        dfs.append(df)

    if n_skipped_files and verbose:
        print(f"    [DAILY] skipped {n_skipped_files} files (all dates already in DB)", flush=True)

    if not dfs:
        print("    [WARN] No new daily data to process", flush=True)
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    combined["code"] = combined["code"].astype(str).str.strip()
    combined = combined.sort_values(["code", "date"]).reset_index(drop=True)

    # Compute MAs over full per-code history (must use ALL rows, not just missing)
    combined["ma5"] = combined.groupby("code", sort=False)["close"].transform(
        lambda x: x.rolling(window=5, min_periods=1).mean()
    ).round(6)
    combined["ma5_ratio"] = ((combined["close"] / combined["ma5"]) - 1.0).round(6)
    combined["ma20"] = combined.groupby("code", sort=False)["close"].transform(
        lambda x: x.rolling(window=20, min_periods=1).mean()
    ).round(6)
    combined["ma60"] = combined.groupby("code", sort=False)["close"].transform(
        lambda x: x.rolling(window=60, min_periods=1).mean()
    ).round(6)
    combined["ma120"] = combined.groupby("code", sort=False)["close"].transform(
        lambda x: x.rolling(window=120, min_periods=1).mean()
    ).round(6)
    combined["ma255"] = combined.groupby("code", sort=False)["close"].transform(
        lambda x: x.rolling(window=255, min_periods=1).mean()
    ).round(6)

    # Filter to missing (date, code) pairs only — this is the key optimization
    mask = combined.apply(lambda r: (r["date"], r["code"]) not in existing_keys, axis=1)
    combined = combined[mask].reset_index(drop=True)

    if verbose:
        print(f"    → {len(combined):,} new rows  ·  {combined['code'].nunique()} indexes", flush=True)
        if len(combined):
            print(f"    → date range: {combined['date'].min()} → {combined['date'].max()}", flush=True)

    return combined


# ============================================================================
# Build 5min intraday DataFrame from tick files (missing pairs only)
# ============================================================================
def resample_ticks_to_5min(tick_df: pd.DataFrame) -> pd.DataFrame:
    if tick_df is None or len(tick_df) == 0:
        return pd.DataFrame()

    tick_df = tick_df.copy()
    tick_df["datetime"] = pd.to_datetime(tick_df["date"] + " " + tick_df["time"], errors="coerce")
    tick_df = tick_df.dropna(subset=["datetime"])
    tick_df = tick_df.sort_values("datetime").reset_index(drop=True)

    tick_df["current"] = pd.to_numeric(tick_df["current"], errors="coerce")
    tick_df["high"] = pd.to_numeric(tick_df["high"], errors="coerce")
    tick_df["low"] = pd.to_numeric(tick_df["low"], errors="coerce")

    tick_df = tick_df.set_index("datetime")

    ohlc = tick_df["current"].resample("5min").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    })

    ohlc = ohlc.dropna(subset=["close"])
    ohlc = ohlc.reset_index()
    ohlc["date"] = ohlc["datetime"].dt.date
    ohlc["time"] = ohlc["datetime"].dt.time

    ohlc["change"] = ohlc["close"] - ohlc["open"]
    ohlc["change_pct"] = (ohlc["change"] / ohlc["open"]) * 100.0

    ohlc = ohlc[["date", "time", "open", "high", "low", "close", "change", "change_pct"]]
    return ohlc


def build_intraday_5min_df(missing_pairs: set, verbose: bool = True) -> pd.DataFrame:
    """Read only intraday CSV files whose (code, date) is in missing_pairs.

    Args:
        missing_pairs: set of (date, code) tuples NOT in stats.index_intraday_5min.
    """
    intraday_files = sorted(glob.glob(os.path.join(CSINDEX_DIR, "*_intraday_*.csv")))
    if verbose:
        print(f"    [INTRADAY] {len(intraday_files)} intraday CSV files available", flush=True)

    if not missing_pairs:
        if verbose:
            print(f"    [INTRADAY] no missing (date, code) pairs — skipping all files", flush=True)
        return pd.DataFrame()

    all_dfs = []
    n_skipped = 0
    n_read = 0
    for path in intraday_files:
        basename = os.path.basename(path)
        m = re.match(r"^([^_]+)_intraday_(\d{8})\.csv$", basename)
        if not m:
            continue
        code = m.group(1)
        date_str = m.group(2)
        date_obj = ymd_to_date(date_str)
        if date_obj is None:
            continue

        # Filter: only read files whose (date, code) is missing from DB
        if (date_obj, code) not in missing_pairs:
            n_skipped += 1
            continue

        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue

        df_5min = resample_ticks_to_5min(df)
        if df_5min.empty:
            continue

        df_5min["code"] = code
        all_dfs.append(df_5min)
        n_read += 1

    if verbose:
        print(f"    [INTRADAY] read {n_read} files, skipped {n_skipped} (already in DB)", flush=True)

    if not all_dfs:
        print("    [WARN] No new intraday data resampled", flush=True)
        return pd.DataFrame()

    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.sort_values(["code", "date", "time"]).reset_index(drop=True)

    if verbose:
        print(f"    → {len(combined):,} new 5min bars  ·  {combined['code'].nunique()} indexes  ·  {combined['date'].nunique()} dates", flush=True)

    return combined


# ============================================================================
# Database insertion
# ============================================================================
async def insert_daily_to_db(conn, daily_df, verbose=True):
    """Insert daily data into database tables (async).

    Caller has already filtered daily_df to missing (date, code) pairs, so
    no further existing_keys check is needed here.
    """
    if daily_df is None or len(daily_df) == 0:
        return 0

    identity_rows = []
    basic_stats_rows = []
    valuation_rows = []
    tech_stats_rows = []

    for _, row in daily_df.iterrows():
        identity_rows.append({
            "date": row["date"],
            "code": row["code"],
            "name": str(row.get("indexName", "")) if pd.notna(row.get("indexName")) else "",
        })
        basic_stats_rows.append({
            "date": row["date"],
            "code": row["code"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
            "amount": row["amount"],
            "change": row["change"],
            "change_pct": row["changePct"],
        })
        valuation_rows.append({
            "date": row["date"],
            "code": row["code"],
            "pe": row["pe"],
            "cons_number": row["consNumber"],
        })
        tech_stats_rows.append({
            "date": row["date"],
            "code": row["code"],
            "ma5": row["ma5"],
            "ma5_ratio": row["ma5_ratio"],
            "ma20": row["ma20"],
            "ma60": row["ma60"],
            "ma120": row["ma120"],
            "ma255": row["ma255"],
        })

    pk = ["date", "code"]
    for tbl, rows in [
        ("stats.index_identity",    identity_rows),
        ("stats.index_basic_stats", basic_stats_rows),
        ("stats.index_valuation",   valuation_rows),
        ("stats.index_tech_stats",  tech_stats_rows),
    ]:
        if rows:
            inserted = await bulk_upsert_async(conn, tbl, rows, pk)
            if verbose:
                print(f"    [DB] Inserted {inserted:,} rows into {tbl}", flush=True)

    return len(identity_rows)


async def insert_intraday_to_db(conn, intraday_df, verbose=True):
    """Insert intraday 5min data into database (async).

    Caller has already filtered intraday_df to missing (date, code) pairs.
    """
    if intraday_df is None or intraday_df.empty:
        return 0

    # Ensure (date, code) pairs exist in index_identity (FK parent).
    # Some intraday files may have dates not yet in index_identity (e.g.
    # daily build failed but intraday succeeded). Insert placeholder
    # identity rows so the FK constraint is satisfied.
    intraday_pairs = set((row["date"], row["code"]) for _, row in intraday_df.iterrows())
    existing_identity = await get_existing_keys_async(
        conn, "stats.index_identity", ["date", "code"]
    )
    missing_identity = intraday_pairs - existing_identity
    if missing_identity:
        placeholder_rows = [{"date": d, "code": c, "name": ""} for d, c in missing_identity]
        await bulk_upsert_async(conn, "stats.index_identity", placeholder_rows, ["date", "code"])
        if verbose:
            print(f"    [DB] Added {len(missing_identity)} placeholder identity rows "
                  f"for intraday-only (date, code) pairs", flush=True)

    # Dedupe within the batch to avoid "ON CONFLICT DO UPDATE cannot affect
    # row a second time" (multiple files may produce the same (date, code, time))
    intraday_df = intraday_df.drop_duplicates(subset=["date", "code", "time"], keep="last")

    rows = []
    for _, row in intraday_df.iterrows():
        rows.append({
            "date": row["date"],
            "code": row["code"],
            "time": row["time"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "change": row["change"],
            "change_pct": row["change_pct"],
        })

    if rows:
        inserted = await bulk_upsert_async(
            conn, "stats.index_intraday_5min", rows, ["date", "code", "time"]
        )
        if verbose:
            print(f"    [DB] Inserted {inserted:,} rows into stats.index_intraday_5min", flush=True)

    return len(rows)


async def sync_has_intraday_flag(conn, verbose=True):
    """Sync index_basic_stats.has_intraday_5mins from index_intraday_5min existence."""
    sql = """
        UPDATE stats.index_basic_stats bs
        SET has_intraday_5mins = TRUE
        FROM (SELECT DISTINCT date, code FROM stats.index_intraday_5min) sub
        WHERE bs.date = sub.date AND bs.code = sub.code
          AND bs.has_intraday_5mins IS NOT TRUE
    """
    result = await conn.execute(sql)
    n = 0
    try:
        n = int(result.split()[-1])
    except Exception:
        pass
    if verbose:
        print(f"    [DB] Synced has_intraday_5mins flag: {n:,} rows set to TRUE", flush=True)
    return n


# ============================================================================
# Main pipeline
# ============================================================================
async def main():
    ap = argparse.ArgumentParser()
    add_common_build_args(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "BUILD CSINDEX DAILY + 5MIN INTRADAY  ·  missing-data-only → DATABASE",
        **{
            "CSIndex dir": CSINDEX_DIR,
            "Today":       TODAY_STR,
        }
    )

    # ------------------------------------------------------------------
    # 1. Connect to DB and query existing keys
    # ------------------------------------------------------------------
    print("\n[1/4] Connecting to database and querying existing keys …", flush=True)
    conn = await get_db_or_exit()

    try:
        if args.force:
            print("    [DB] Force mode: truncating existing tables", flush=True)
            for tbl in ("stats.index_intraday_5min", "stats.index_tech_stats",
                        "stats.index_valuation", "stats.index_basic_stats",
                        "stats.index_identity"):
                await truncate_table_async(conn, tbl)
            existing_daily_keys = set()
            existing_intraday_pairs = set()
        else:
            existing_daily_keys = await get_existing_keys_async(
                conn, "stats.index_identity", ["date", "code"]
            )
            print(f"    [DB] {len(existing_daily_keys):,} existing (date, code) pairs in stats.index_identity", flush=True)

            # For intraday, query DISTINCT (date, code) from index_intraday_5min.
            # This is much smaller than querying all (date, code, time) triples.
            intraday_pairs_rows = await conn.fetch(
                "SELECT DISTINCT date, code FROM stats.index_intraday_5min"
            )
            existing_intraday_pairs = {(r["date"], r["code"]) for r in intraday_pairs_rows}
            print(f"    [DB] {len(existing_intraday_pairs):,} existing (date, code) pairs in stats.index_intraday_5min", flush=True)

        # ------------------------------------------------------------------
        # 2. Build daily frame (filtered to missing keys)
        # ------------------------------------------------------------------
        print("\n[2/4] Building daily history frame (missing keys only) …", flush=True)
        daily_df = build_daily_df(existing_daily_keys)

        # ------------------------------------------------------------------
        # 3. Build intraday frame (filtered to missing pairs)
        # ------------------------------------------------------------------
        print("\n[3/4] Building 5min intraday frame (missing pairs only) …", flush=True)

        # Compute missing intraday pairs: all (date, code) from intraday files
        # MINUS existing pairs in index_intraday_5min.
        # We scan filenames (not read files) to discover available pairs.
        intraday_files = sorted(glob.glob(os.path.join(CSINDEX_DIR, "*_intraday_*.csv")))
        available_intraday_pairs = set()
        for path in intraday_files:
            basename = os.path.basename(path)
            m = re.match(r"^([^_]+)_intraday_(\d{8})\.csv$", basename)
            if not m:
                continue
            code = m.group(1)
            date_obj = ymd_to_date(m.group(2))
            if date_obj:
                available_intraday_pairs.add((date_obj, code))

        missing_intraday_pairs = available_intraday_pairs - existing_intraday_pairs
        print(f"    → {len(available_intraday_pairs)} (date, code) pairs available, "
              f"{len(missing_intraday_pairs)} missing", flush=True)

        intraday_df = build_intraday_5min_df(missing_intraday_pairs)

        # ------------------------------------------------------------------
        # 4. Insert to database
        # ------------------------------------------------------------------
        print("\n[4/4] Inserting data to database …", flush=True)
        new_daily = await insert_daily_to_db(conn, daily_df)
        new_intraday = await insert_intraday_to_db(conn, intraday_df)
        await sync_has_intraday_flag(conn)

        print(f"    → Total new daily rows inserted: {new_daily:,}", flush=True)
        print(f"    → Total new intraday rows inserted: {new_intraday:,}", flush=True)

    finally:
        await conn.close()

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
