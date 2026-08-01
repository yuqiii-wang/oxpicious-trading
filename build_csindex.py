"""
build_csindex.py — Build CSIndex daily history to DATABASE
(missing-data-only, no intermediate CSV).

Reads the history CSV archive produced by download_csindex.py:
  • {code}_history.csv        (daily OHLCV + PE + amount)

Also reads SZSE index daily CSVs produced by download_szse_archive.py and
download_szse_trend.py (TABKEY=tab7 指数), filtered to three SZSE
benchmarks:
  • temps/szse_archive/szse_index_YYYYMMDD.csv       (399001, 399006, 399237)
  • temps/szse_trend/szse_trend_index_YYYYMMDD.csv    (399001, 399006, 399237)

SZSE columns (交易日期, 指数代码, ...) are mapped to the CSIndex schema
(date, indexCode, ...). Fields not provided by SZSE (volume, pe,
consNumber) are set to NULL; absolute change is computed as close − prev_close.

Computes moving averages (ma5, ma20, ma60, ma120, ma255) from daily close.

NOTE: 5-minute intraday bars (stats.index_intraday_5min) are NO LONGER built
here. Intraday is now streamed in real time by stream_sse_price.py from the
SSE report page (https://www.sse.com.cn/market/price/report/ 指数 tab),
filtered to indices already present in stats.index_identity. The former
{code}_intraday_{date}.csv tick files and the resample_ticks_to_5min /
build_intraday_5min_df / insert_intraday_to_db / sync_has_intraday_flag
helpers have been removed. The has_intraday_5mins flag on index_basic_stats
is now synced by stream_sse_price.sync_index_has_intraday_flag after each
index bar lands.

Missing-data detection flow:
  DAILY:
    1. Query stats.index_identity for existing (date, code) pairs
    2. Read each {code}_history.csv (full history — needed for MA computation)
    3. Read SZSE index CSVs (archive + trend) for codes 399001 / 399006 / 399237
    4. Compute MAs over the full per-code history
    5. Filter rows to (date, code) pairs NOT in existing_keys
    6. Bulk upsert only the missing rows

With --force: truncate the 4 daily index_* tables first, so all source data
is treated as missing. (stats.index_intraday_5min is owned by
stream_sse_price.py and is NOT truncated here.)

Inserts to database tables:
  • index_identity          (date, code, name)
  • index_basic_stats       (date, code, OHLCV, volume, amount, change)
  • index_valuation         (date, code, PE, consNumber)
  • index_tech_stats        (date, code, MAs)

Usage:
  python build_csindex.py
  python build_csindex.py --force   (rebuild all daily tables)
"""
import os, sys, glob, time, argparse
import datetime
import re

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _build_commons import (
    setup_utf8_stdout, add_common_build_args, get_db_or_exit,
    parse_num, parse_date,
    print_build_header, print_wall_time,
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
SZSE_INDEX_CODES = {"399001", "399006", "399237"}  # 深证成指, 创业板指, 运输指数

# Minimum shared-weight threshold for proxy index selection during close
# estimation.  If no index has > 60% composition overlap with the target,
# the missing close is carried forward from the previous trading day.
SHARED_WEIGHT_THRESHOLD = 60.0

# DB check constraint chk_index_identity_code_format: code must be 6 digits or
# H + 5 digits.  CSIndex publishes a few indices with non-conforming codes
# (e.g. CES100 中华港股通精选100) that would violate the constraint, so they
# are skipped during build.
VALID_CODE_RE = re.compile(r'^(\d{6}|H\d{5})$')


# ============================================================================
# Fetch index shared weights from sec_composition (for close estimation)
# ============================================================================
async def fetch_index_shared_weights(conn) -> dict:
    """Compute composition shared weight for every (index_a, index_b) pair.

    Uses the LATEST composition snapshot in stats.sec_composition for each
    index code.  For stocks held by BOTH indices, sums the weight_pct of
    index_a (the subject's shared weight).  Returns a dict:
        { (code_a, code_b): shared_weight_a }
    where shared_weight_a = Σ w_a on stocks held by both a and b.

    This is used to find the best proxy index for close-price estimation:
    when index A is missing a trading day, we pick the index B with the
    highest shared weight (> 60%) and use B's daily change to estimate
    A's close.
    """
    rows = await conn.fetch("""
        WITH latest AS (
            SELECT code, source_type, MAX(snapshot_date) AS max_date
            FROM stats.sec_composition
            WHERE stock_code IS NOT NULL
              AND source_type = 'index'
            GROUP BY code, source_type
        ),
        holdings AS (
            SELECT sc.code, LEFT(sc.stock_code, 6) AS normalized_code,
                   sc.weight_pct
            FROM stats.sec_composition sc
            JOIN latest ld ON sc.code = ld.code
                          AND sc.source_type = ld.source_type
                          AND sc.snapshot_date = ld.max_date
            WHERE sc.stock_code IS NOT NULL
        )
        SELECT
            h1.code AS code_a,
            h2.code AS code_b,
            SUM(h1.weight_pct) AS shared_weight_a
        FROM holdings h1
        JOIN holdings h2 ON h1.normalized_code = h2.normalized_code
        WHERE h1.code != h2.code
        GROUP BY h1.code, h2.code
    """)
    result = {}
    for r in rows:
        sw = float(r["shared_weight_a"])
        if sw != sw:  # NaN check
            continue
        result[(r["code_a"], r["code_b"])] = sw
    return result


# ============================================================================
# Fill missing close prices for indices with date gaps
# ============================================================================
def _fill_missing_closes(combined: pd.DataFrame,
                         shared_weights: dict,
                         verbose: bool = True) -> pd.DataFrame:
    """Fill missing trading days with estimated close prices.

    Some indices (e.g. 399001 深证成指) are missing trading days that other
    indices have — holidays, late starts, data gaps.  When the analysis
    script pivots to wide format, these gaps become NaN, causing widespread
    NULL correlations.

    This function:
    1. Builds a complete date grid (union of all dates in `combined`).
    2. For each code, finds dates in the grid that are missing.
    3. For each missing (date, code):
       a. Finds the best proxy index = the index with the highest shared
          weight (> 60%) that HAS data for that date.
       b. If a proxy is found: estimated_close = prev_close * (1 + proxy_pct_change / 100)
       c. If no proxy qualifies: estimated_close = prev_close (carry forward).
    4. Appends estimated rows to `combined` with is_close_estimated=True.

    Original rows get is_close_estimated=False.

    Returns the augmented DataFrame.
    """
    if combined.empty:
        combined["is_close_estimated"] = False
        return combined

    # Mark original rows as non-estimated
    combined["is_close_estimated"] = False

    # Build complete date grid (union of all dates across all codes)
    all_dates = sorted(combined["date"].unique())
    date_set = set(all_dates)

    # For each code, find missing dates
    codes = sorted(combined["code"].unique())
    estimated_rows = []

    # Build a lookup: (date, code) -> row data for quick proxy lookup
    # We need changePct for each (date, code) to use as proxy
    date_code_lookup = {}
    for _, row in combined.iterrows():
        date_code_lookup[(row["date"], row["code"])] = row

    # Build per-code proxy mapping: code -> list of (proxy_code, shared_weight) sorted desc
    proxy_map = {}
    for code in codes:
        candidates = []
        for (ca, cb), sw in shared_weights.items():
            if ca == code and cb in codes:
                candidates.append((cb, sw))
        candidates.sort(key=lambda x: x[1], reverse=True)
        proxy_map[code] = candidates

    n_filled = 0
    n_carry = 0

    for code in codes:
        sub = combined[combined["code"] == code].sort_values("date").reset_index(drop=True)
        existing_dates = set(sub["date"].unique())
        missing_dates = date_set - existing_dates

        if not missing_dates:
            continue

        # Build a date -> prev_close map for this code
        # We need prev_close for each missing date. Since missing dates
        # are interspersed, we process chronologically and carry forward.
        sub_sorted = sub.sort_values("date").reset_index(drop=True)
        # Use a plain dict for close tracking (supports mutation)
        close_map = dict(zip(sub_sorted["date"], sub_sorted["close"]))
        index_name = str(sub_sorted["indexName"].iloc[0]) if "indexName" in sub_sorted.columns and len(sub_sorted) else ""

        # Get this code's proxy candidates
        proxies = proxy_map.get(code, [])

        for missing_date in sorted(missing_dates):
            # Find prev_close: the last known close before missing_date
            known_before = {d: c for d, c in close_map.items() if d < missing_date}
            if not known_before:
                # No prior data — skip (can't estimate without a base)
                continue
            prev_date = max(known_before.keys())
            prev_close = known_before[prev_date]

            # Try to find a proxy index with data for this date
            estimated_close = None
            proxy_used = None

            for proxy_code, sw in proxies:
                if sw < SHARED_WEIGHT_THRESHOLD:
                    break  # sorted desc, so no more qualify
                proxy_row = date_code_lookup.get((missing_date, proxy_code))
                if proxy_row is not None:
                    proxy_pct = proxy_row.get("changePct")
                    if proxy_pct is not None and pd.notna(proxy_pct):
                        estimated_close = prev_close * (1.0 + float(proxy_pct) / 100.0)
                        proxy_used = proxy_code
                        break

            if estimated_close is None:
                # No proxy found — carry forward prev_close
                estimated_close = prev_close
                n_carry += 1
            else:
                n_filled += 1

            est_row = {
                "date": missing_date,
                "code": code,
                "indexCode": code,
                "indexName": index_name,
                "open": None,
                "high": None,
                "low": None,
                "close": round(estimated_close, 4),
                "volume": None,
                "amount": None,
                "change": round(estimated_close - prev_close, 4),
                "changePct": round((estimated_close - prev_close) / prev_close * 100.0, 4) if prev_close else None,
                "pe": None,
                "consNumber": None,
                "is_close_estimated": True,
            }
            estimated_rows.append(est_row)

            # Update close_map so subsequent missing dates can chain
            close_map[missing_date] = estimated_close

    if estimated_rows:
        est_df = pd.DataFrame(estimated_rows)
        combined = pd.concat([combined, est_df], ignore_index=True)
        combined = combined.sort_values(["code", "date"]).reset_index(drop=True)

    if verbose:
        print(f"    [EST] Close estimation: {n_filled} dates filled via proxy, "
              f"{n_carry} carried forward, {len(estimated_rows)} total estimated rows", flush=True)

    return combined


# ============================================================================
# Load SZSE index daily CSVs (archive + trend) → CSIndex schema
# ============================================================================
def _load_szse_index_history(verbose: bool = True) -> list:
    """Load SZSE index daily CSVs (archive + trend) and map to CSIndex schema.

    Scans two directories for per-date index CSV files:
      • temps/szse_archive/szse_index_YYYYMMDD.csv       (historical archive)
      • temps/szse_trend/szse_trend_index_YYYYMMDD.csv   (recent trend)

    Each CSV contains ~180 indexes for one date; this function keeps only
    399001 (深证成指), 399006 (创业板指), and 399237 (运输指数) and maps
    columns to the CSIndex history schema so they can be concatenated with
    CSIndex DataFrames.

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
def build_daily_df(existing_keys: set, shared_weights: dict = None,
                   verbose: bool = True) -> pd.DataFrame:
    """Read all *_history.csv files, compute MAs, filter to missing (date, code) pairs.

    Args:
        existing_keys: set of (date, code) tuples already in stats.index_identity.
                       Rows matching these keys are skipped before insert.
        shared_weights: dict of {(code_a, code_b): shared_weight} from
                        sec_composition.  Used to fill missing trading days
                        with estimated close prices.  If None, no estimation
                        is performed.

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
        # Skip codes that violate the DB check constraint (e.g. CES100)
        if not VALID_CODE_RE.match(code):
            n_skipped_files += 1
            continue
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

    # Also load SZSE index data (archive + trend) for 399001 / 399006 / 399237
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

    # Fill missing trading days with estimated close prices (if shared weights available)
    if shared_weights:
        combined = _fill_missing_closes(combined, shared_weights, verbose=verbose)

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
            "is_close_estimated": bool(row.get("is_close_estimated", False)),
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


# ============================================================================
# Main pipeline
# ============================================================================
async def main():
    ap = argparse.ArgumentParser()
    add_common_build_args(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "BUILD CSINDEX DAILY  ·  missing-data-only → DATABASE",
        **{
            "CSIndex dir": CSINDEX_DIR,
            "Today":       TODAY_STR,
        }
    )

    # ------------------------------------------------------------------
    # 1. Connect to DB and query existing keys
    # ------------------------------------------------------------------
    print("\n[1/3] Connecting to database and querying existing keys …", flush=True)
    conn = await get_db_or_exit()

    try:
        if args.force:
            print("    [DB] Force mode: truncating existing daily tables", flush=True)
            # NOTE: stats.index_intraday_5min is owned by stream_sse_price.py
            # (real-time SSE streaming) and is intentionally NOT truncated here.
            for tbl in ("stats.index_tech_stats",
                        "stats.index_valuation", "stats.index_basic_stats",
                        "stats.index_identity"):
                await truncate_table_async(conn, tbl)
            existing_daily_keys = set()
        else:
            existing_daily_keys = await get_existing_keys_async(
                conn, "stats.index_identity", ["date", "code"]
            )
            print(f"    [DB] {len(existing_daily_keys):,} existing (date, code) pairs in stats.index_identity", flush=True)

        # ------------------------------------------------------------------
        # 2. Build daily frame (filtered to missing keys)
        # ------------------------------------------------------------------
        print("\n[2/3] Building daily history frame (missing keys only) …", flush=True)

        # Fetch shared weights for close-price estimation of missing dates
        shared_weights = await fetch_index_shared_weights(conn)
        print(f"    [DB] {len(shared_weights):,} index shared-weight pairs loaded "
              f"for close estimation", flush=True)

        daily_df = build_daily_df(existing_daily_keys, shared_weights=shared_weights)

        # ------------------------------------------------------------------
        # 3. Insert to database
        # ------------------------------------------------------------------
        print("\n[3/3] Inserting daily data to database …", flush=True)
        new_daily = await insert_daily_to_db(conn, daily_df)

        print(f"    → Total new daily rows inserted: {new_daily:,}", flush=True)

    finally:
        await conn.close()

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
