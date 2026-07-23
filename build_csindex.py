"""
build_csindex.py — Build CSIndex daily history + 5min intraday to DATABASE.

Reads the history and intraday CSV archives produced by download_csindex.py:
  • {code}_history.csv        (daily OHLCV + PE + turnover)
  • {code}_intraday_{date}.csv (intraday ticks at ~15s intervals)

Resamples intraday ticks to 5-minute OHLCV bars and merges with daily data.
Computes moving averages (ma5, ma20, ma60, ma120, ma255) from daily close.

Inserts to database tables:
  • index_identity          (date, code, name)
  • index_basic_stats       (date, code, OHLCV, volume, turnover, change)
  • index_valuation         (date, code, PE, consNumber)
  • index_tech_stats        (date, code, MAs)
  • index_intraday_5min     (date, code, time, OHLC, change)

Only inserts new data not already present in the database.

Usage:
  python build_csindex.py
  python build_csindex.py --force   (rebuild all)
"""
import os, sys, re, glob, time, argparse
import datetime
from pathlib import Path

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _download_commons import read_csv_preferred
from _db_commons import (
    get_db_connection_async, get_existing_keys_async, bulk_upsert_async,
    truncate_table_async
)

# ============================================================================
# Paths
# ============================================================================
PROJECT_ROOT        = os.path.dirname(os.path.abspath(__file__))
CSINDEX_DIR         = os.path.join(PROJECT_ROOT, "temps", "csindex")

TODAY_STR = datetime.datetime.now().strftime("%Y-%m-%d")

# ============================================================================
# Helpers
# ============================================================================
def parse_num(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        try:
            v = float(s)
            return None if not np.isfinite(v) else v
        except Exception:
            return None
    txt = str(s).strip()
    if not txt or txt in ("--", "-", "—", "null", "NULL", "None", "nan", "NaN"):
        return None
    txt = txt.replace(",", "").replace("，", "").replace(" ", "").replace("\u3000", "")
    try:
        v = float(txt)
        return None if not np.isfinite(v) else v
    except Exception:
        return None


def parse_date(val):
    """Parse a date value into a datetime.date object.

    Returns None on failure. Always returns datetime.date (not str) so
    asyncpg can encode it for a DATE column without raising
    "expected a date instance, got 'str'".
    """
    s = str(val).strip()
    if not s:
        return None
    s = s.replace("-", "").replace("/", "")
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    return None


def parse_time(val):
    """Parse a HH:MM:SS string into a datetime.time object (asyncpg-compatible)."""
    if val is None or isinstance(val, datetime.time):
        return val
    s = str(val).strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return datetime.time(int(parts[0]), int(parts[1]), int(parts[2]))
        if len(parts) == 2:
            return datetime.time(int(parts[0]), int(parts[1]))
    except ValueError:
        return None
    return None


# ============================================================================
# Build daily history DataFrame
# ============================================================================
def build_daily_df(verbose=True):
    history_files = sorted(glob.glob(os.path.join(CSINDEX_DIR, "*_history.csv")))
    if verbose:
        print(f"    [DAILY] {len(history_files)} history CSVs in {CSINDEX_DIR}", flush=True)

    dfs = []
    for path in history_files:
        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue

        code = os.path.basename(path).replace("_history.csv", "")
        df["code"] = code
        dfs.append(df)

    if not dfs:
        print("    [WARN] No history data read", flush=True)
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)

    # Ensure code is always string to preserve leading zeros (e.g., "000001")
    combined["code"] = combined["code"].astype(str).str.strip()

    for col in ["open", "high", "low", "close", "volume", "turnover", "change", "changePct", "pe", "consNumber"]:
        if col in combined.columns:
            combined[col] = combined[col].apply(parse_num)

    combined["date"] = combined["date"].apply(parse_date)
    combined = combined.dropna(subset=["date"])

    combined = combined.sort_values(["code", "date"]).reset_index(drop=True)

    if verbose:
        print(f"    → {len(combined):,} rows  ·  {combined['code'].nunique()} indexes", flush=True)
        print(f"    → date range: {combined['date'].min()} → {combined['date'].max()}", flush=True)

    return combined


# ============================================================================
# Build 5min intraday DataFrame from tick files
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


def build_intraday_5min_df(verbose=True):
    intraday_files = sorted(glob.glob(os.path.join(CSINDEX_DIR, "*_intraday_*.csv")))
    if verbose:
        print(f"    [INTRADAY] {len(intraday_files)} intraday CSV files in {CSINDEX_DIR}", flush=True)

    all_dfs = []
    for path in intraday_files:
        basename = os.path.basename(path)
        m = re.match(r"^([^_]+)_intraday_(\d{8})\.csv$", basename)
        if not m:
            continue
        code = m.group(1)
        date_str = m.group(2)

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

    if not all_dfs:
        print("    [WARN] No intraday data resampled", flush=True)
        return pd.DataFrame()

    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.sort_values(["code", "date", "time"]).reset_index(drop=True)

    if verbose:
        print(f"    → {len(combined):,} 5min bars  ·  {combined['code'].nunique()} indexes  ·  {combined['date'].nunique()} dates", flush=True)

    return combined


# ============================================================================
# Database insertion
# ============================================================================
async def insert_daily_to_db(conn, daily_df, verbose=True):
    """Insert daily data into database tables (async)."""
    
    # Get existing keys
    existing_keys = await get_existing_keys_async(conn, "stats.index_identity", ["date", "code"])
    if verbose:
        print(f"    [DB] {len(existing_keys):,} existing (date, code) pairs in stats.index_identity", flush=True)
    
    # Convert to list of dicts for each table
    identity_rows = []
    basic_stats_rows = []
    valuation_rows = []
    tech_stats_rows = []
    
    for _, row in daily_df.iterrows():
        key = (row["date"], row["code"])
        if key in existing_keys:
            continue
        
        # index_identity
        identity_rows.append({
            "date": row["date"],
            "code": row["code"],
            "name": str(row.get("indexName", "")) if pd.notna(row.get("indexName")) else "",
        })
        
        # index_basic_stats
        basic_stats_rows.append({
            "date": row["date"],
            "code": row["code"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
            "turnover": row["turnover"],
            "change": row["change"],
            "change_pct": row["changePct"],
        })
        
        # index_valuation
        valuation_rows.append({
            "date": row["date"],
            "code": row["code"],
            "pe": row["pe"],
            "cons_number": row["consNumber"],
        })
        
        # index_tech_stats
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
    
    # Bulk upsert each table
    if identity_rows:
        inserted = await bulk_upsert_async(conn, "stats.index_identity", identity_rows, ["date", "code"])
        if verbose:
            print(f"    [DB] Inserted {inserted:,} rows into stats.index_identity", flush=True)
    
    if basic_stats_rows:
        inserted = await bulk_upsert_async(conn, "stats.index_basic_stats", basic_stats_rows, ["date", "code"])
        if verbose:
            print(f"    [DB] Inserted {inserted:,} rows into stats.index_basic_stats", flush=True)
    
    if valuation_rows:
        inserted = await bulk_upsert_async(conn, "stats.index_valuation", valuation_rows, ["date", "code"])
        if verbose:
            print(f"    [DB] Inserted {inserted:,} rows into stats.index_valuation", flush=True)
    
    if tech_stats_rows:
        inserted = await bulk_upsert_async(conn, "stats.index_tech_stats", tech_stats_rows, ["date", "code"])
        if verbose:
            print(f"    [DB] Inserted {inserted:,} rows into stats.index_tech_stats", flush=True)
    
    return len(identity_rows)


async def insert_intraday_to_db(conn, intraday_df, verbose=True):
    """Insert intraday 5min data into database (async)."""
    
    if intraday_df.empty:
        return 0
    
    # First, ensure all (date, code) pairs exist in index_identity
    # Extract all unique (date, code) pairs from intraday data
    intraday_date_codes = set((row["date"], row["code"]) for _, row in intraday_df.iterrows())
    
    # Get existing (date, code) pairs from stats.index_identity
    existing_date_codes = await get_existing_keys_async(conn, "stats.index_identity", ["date", "code"])
    
    # Find missing pairs
    missing_date_codes = intraday_date_codes - existing_date_codes
    
    if missing_date_codes and verbose:
        print(f"    [DB] Adding {len(missing_date_codes):,} missing (date, code) pairs to stats.index_identity", flush=True)
    
    # Insert missing pairs into stats.index_identity
    if missing_date_codes:
        identity_rows = []
        for date, code in missing_date_codes:
            # Get name from the first occurrence in intraday data
            name = intraday_df[(intraday_df["date"] == date) & (intraday_df["code"] == code)]["name"].iloc[0] if "name" in intraday_df.columns else ""
            identity_rows.append({
                "date": date,
                "code": code,
                "name": name,
            })
        
        await bulk_upsert_async(conn, "stats.index_identity", identity_rows, ["date", "code"])
    
    # Get existing keys
    existing_keys = await get_existing_keys_async(conn, "stats.index_intraday_5min", ["date", "code", "time"])
    if verbose:
        print(f"    [DB] {len(existing_keys):,} existing (date, code, time) pairs in stats.index_intraday_5min", flush=True)
    
    # Convert to list of dicts
    rows = []
    for _, row in intraday_df.iterrows():
        key = (row["date"], row["code"], row["time"])
        if key in existing_keys:
            continue
        
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
    
    # Bulk upsert
    if rows:
        inserted = await bulk_upsert_async(conn, "stats.index_intraday_5min", rows, ["date", "code", "time"])
        if verbose:
            print(f"    [DB] Inserted {inserted:,} rows into stats.index_intraday_5min", flush=True)

    return len(rows)


async def sync_has_intraday_flag(conn, verbose=True):
    """Sync index_basic_stats.has_intraday_5mins from index_intraday_5min existence.

    Sets the flag to TRUE for every (date, code) that has at least one row in
    stats.index_intraday_5min. Idempotent: only touches rows whose flag is not
    already TRUE. Run after both daily and intraday inserts so that existing
    daily rows (skipped by insert_daily_to_db) still get the flag updated when
    intraday data appears for them.
    """
    sql = """
        UPDATE stats.index_basic_stats bs
        SET has_intraday_5mins = TRUE
        FROM (SELECT DISTINCT date, code FROM stats.index_intraday_5min) sub
        WHERE bs.date = sub.date AND bs.code = sub.code
          AND bs.has_intraday_5mins IS NOT TRUE
    """
    result = await conn.execute(sql)
    # asyncpg execute() returns a status string like "UPDATE 12"
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
    import asyncio
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="Rebuild all data (truncate tables first)")
    args = ap.parse_args()

    t0 = time.time()
    print("=" * 78, flush=True)
    print("  BUILD CSINDEX DAILY + 5MIN INTRADAY TO DATABASE", flush=True)
    print("=" * 78, flush=True)
    print(f"  CSIndex dir: {CSINDEX_DIR}", flush=True)
    print(f"  Today      : {TODAY_STR}", flush=True)

    # Build daily dataframe
    print("\n[1/4] Building daily history frame from *_history.csv …", flush=True)
    daily_df = build_daily_df()
    if len(daily_df) == 0:
        print("    [FATAL] No daily rows parsed", flush=True)
        sys.exit(1)

    # Compute MAs
    print("\n[2/4] Computing MA statistics (close-based) …", flush=True)
    daily_df = daily_df.sort_values(["code", "date"]).reset_index(drop=True)
    daily_df["ma5"] = daily_df.groupby("code", sort=False)["close"].transform(
        lambda x: x.rolling(window=5, min_periods=1).mean()
    ).round(6)
    daily_df["ma5_ratio"] = ((daily_df["close"] / daily_df["ma5"]) - 1.0).round(6)
    daily_df["ma20"] = daily_df.groupby("code", sort=False)["close"].transform(
        lambda x: x.rolling(window=20, min_periods=1).mean()
    ).round(6)
    daily_df["ma60"] = daily_df.groupby("code", sort=False)["close"].transform(
        lambda x: x.rolling(window=60, min_periods=1).mean()
    ).round(6)
    daily_df["ma120"] = daily_df.groupby("code", sort=False)["close"].transform(
        lambda x: x.rolling(window=120, min_periods=1).mean()
    ).round(6)
    daily_df["ma255"] = daily_df.groupby("code", sort=False)["close"].transform(
        lambda x: x.rolling(window=255, min_periods=1).mean()
    ).round(6)
    print(f"    → MA columns added: ma5, ma5_ratio, ma20, ma60, ma120, ma255", flush=True)

    # Build intraday dataframe
    print("\n[3/4] Building 5min intraday frame from *_intraday_*.csv …", flush=True)
    intraday_df = build_intraday_5min_df()

    # Connect to database (async)
    print("\n[0/4] Connecting to database …", flush=True)
    try:
        conn = await get_db_connection_async()
        print("    [DB] Connected successfully", flush=True)
    except Exception as e:
        print(f"    [FATAL] Database connection failed: {e}", flush=True)
        sys.exit(1)
    
    try:
        # Insert to database
        print("\n[4/4] Inserting data to database …", flush=True)
        
        if args.force:
            print("    [DB] Force mode: truncating existing tables", flush=True)
            await truncate_table_async(conn, "stats.index_intraday_5min")
            await truncate_table_async(conn, "stats.index_tech_stats")
            await truncate_table_async(conn, "stats.index_valuation")
            await truncate_table_async(conn, "stats.index_basic_stats")
            await truncate_table_async(conn, "stats.index_identity")
        
        new_daily = await insert_daily_to_db(conn, daily_df)
        new_intraday = await insert_intraday_to_db(conn, intraday_df)
        await sync_has_intraday_flag(conn)

        print(f"    → Total new daily rows inserted: {new_daily:,}", flush=True)
        print(f"    → Total new intraday rows inserted: {new_intraday:,}", flush=True)
    finally:
        await conn.close()

    print(f"\n  Wall time: {int(time.time()-t0)}s", flush=True)
    print("=" * 78, flush=True)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
