"""
build_szse_sse_bse_stocks.py — Build combined SZSE + SSE + BSE individual-stock
OHLCV + PE data and insert directly to the database (no intermediate CSV).

The SZSE/SSE/BSE daily market files (download_szse_archive.py /
download_szse_trend.py / download_sse_price.py / download_bse_price.py with
security_type="stock") already contain per-stock OHLCV and 市盈率 (PE) for
every Shenzhen/Shanghai/Beijing-listed stock from 2022-01-01 onward. This
script reads only the source CSVs whose dates are MISSING from the database
and inserts them.

Missing-data detection flow:
  1. Glob all source CSV files across the 4 archive/trend directories
  2. Extract available dates from filenames
  3. Query stats.stock_identity for existing dates in the DB
  4. missing_dates = available_dates - existing_dates
  5. Read ONLY source files whose YMD ∈ missing_dates
  6. Dedupe by (date, code) keeping last (archive + trend may overlap)
  7. Bulk upsert into stats.stock_identity + stats.stock_basic_stats

With --force: truncate both tables first, so all source files are treated
as missing.

CRITICAL: Stock codes must be disambiguated with exchange suffixes (.SS for
Shanghai, .SZ for Shenzhen, .BJ for Beijing) because 000xxx/001xxx codes
overlap:
  - SSE (Shanghai): 000xxx codes are INDICES (e.g., 000001 = SSE Composite)
  - SZSE (Shenzhen): 000xxx codes are individual stocks (e.g., 000001 = 平安银行)
  - BSE (Beijing): 43xxxx / 83xxxx / 87xxxx / 920xxx codes are individual stocks

Notes:
  - 证券代码 is stored without leading zeros in the source CSV ("1" → "000001");
    we zero-pad to 6 digits to match composition stock_code.
  - Volume/amount use comma thousands separators; read with thousands=','.
  - Holiday files contain a single "没有找到符合条件的数据！" placeholder row;
    filtered out by requiring a numeric stock code.
  - 市盈率 == 0 marks loss-making stocks (SZSE convention); treated as
    NULL pe (not a real PE value) and falls through to PE estimation.
  - SSE price endpoint does not publish PE data. SSE PE is merged from
    separate per-stock {code}_pe.csv files in temps/sse_archive/ (produced by
    download_sse_price.py's trading-stats mode). For dates with no PE snapshot,
    PE is ESTIMATED from the last actual PE row using a constant-EPS
    assumption: estimated_pe = today_close * last_pe / last_close. The
    is_pe_estimated column marks whether pe is actual (FALSE) or estimated
    (TRUE); rows where no prior actual PE exists within PE_ESTIMATE_MAX_MONTHS
    (3 calendar months) get NULL pe with is_pe_estimated = FALSE.
  - Two-pass insert: rows with actual PE are inserted FIRST (so the DB has
    the latest actual PE for each stock), then rows with missing PE are
    estimated by querying the DB for the last actual PE before that date.
  - is_in_index_or_etf: for each (date, code) inserted into stock_identity, the flag
    is set TRUE when the stock appears in ANY ETF's active composition
    (most recent sec_composition snapshot on or before the date) with
    weight_pct > 0.1. Snapshots are forward-filled (merge_asof semantics).

Usage:
  python build_szse_sse_bse_stocks.py
  python build_szse_sse_bse_stocks.py --start-date 2024-01-01 --end-date 2026-07-23
  python build_szse_sse_bse_stocks.py --force
"""
import os
import sys
import time
import argparse
import bisect
import datetime
from collections import Counter, defaultdict
from dateutil.relativedelta import relativedelta

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from downloads._common.core import add_exchange_suffix
from utils.build_commons import (
    setup_utf8_stdout, add_common_build_args, get_db_or_exit,
    find_missing_dates, glob_source_files, ymd_from_filename, ymd_to_date,
    filter_source_files_by_missing_dates, select_source_files_in_range,
    print_build_header, print_wall_time, PROJECT_ROOT, TODAY_STR,
    bulk_upsert_async, truncate_table_async,
)

setup_utf8_stdout()

import asyncio

# ============================================================================
# Paths
# ============================================================================
SZSE_ARCHIVE_DIR  = os.path.join(PROJECT_ROOT, "temps", "szse_archive")
SZSE_TREND_DIR    = os.path.join(PROJECT_ROOT, "temps", "szse_trend")
SSE_TREND_DIR     = os.path.join(PROJECT_ROOT, "temps", "sse_trend")
BSE_TREND_DIR     = os.path.join(PROJECT_ROOT, "temps", "bse_trend")
# SSE PE files are per-stock ({code}_pe.csv) in the sse_archive dir, separate
# from the daily trend CSVs (SSE dayk endpoint does not publish PE).
SSE_PE_DIR        = os.path.join(PROJECT_ROOT, "temps", "sse_archive")

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

# Maximum age (in calendar months) of a baseline actual-PE row usable for
# estimating PE on a missing-PE date. Beyond this horizon the constant-EPS
# assumption breaks down, so pe is left NULL with is_pe_estimated=false.
PE_ESTIMATE_MAX_MONTHS = 3


# ============================================================================
# Source-file discovery + per-file parsing
# ============================================================================
# Each entry: (directory, glob_pattern, filename_prefix, market_label)
SOURCE_FILE_SETS = [
    (SZSE_ARCHIVE_DIR, "szse_stock_*.csv",        "szse_stock_",        "深圳"),
    (SZSE_TREND_DIR,   "szse_trend_stock_*.csv",  "szse_trend_stock_",  "深圳"),
    (SSE_TREND_DIR,    "sse_trend_stock_*.csv",   "sse_trend_stock_",   "上海"),
    (BSE_TREND_DIR,    "bse_trend_stock_*.csv",   "bse_trend_stock_",   "北京"),
]


def discover_source_files(start_date=None, end_date=None):
    """Glob all source CSV files across the 4 directories, filtered by date range.

    Returns a list of (path, market) tuples.
    """
    out = []
    for scan_dir, pattern, prefix, market in SOURCE_FILE_SETS:
        files = glob_source_files(scan_dir, pattern)
        files = select_source_files_in_range(files, prefix, start_date, end_date)
        for f in files:
            out.append((f, market))
    return out


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
    # Treat "-", empty, and 0.0 as NULL for pe: pd.to_numeric already turns
    # "-" / "" into NaN; additionally normalize 0.0 to NaN since SZSE uses
    # 市盈率=0 as a loss-making marker rather than a real PE value.
    if "pe" in out.columns:
        out["pe"] = out["pe"].where(out["pe"] != 0, np.nan)
    return out


# ============================================================================
# Build rows for missing dates only
# ============================================================================
def build_missing_rows(file_market_pairs, verbose=True):
    """Read the given source files (already filtered to missing dates),
    concatenate, dedupe by (date, code) keeping last, return a DataFrame.
    """
    counts = Counter()
    frames = []
    for path, market in file_market_pairs:
        df = _read_one(path, market)
        if df is None or df.empty:
            counts["empty"] += 1
            continue
        counts["ok"] += 1
        counts["rows"] += len(df)
        frames.append(df)

    if not frames:
        if verbose:
            print("    [INFO] No stock rows parsed from any CSV", flush=True)
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
    combined = combined.dropna(subset=["date"])
    combined = combined.sort_values(["date", "code"]).reset_index(drop=True)
    # Dedupe by (date, code) keeping last — szse_archive and szse_trend may
    # overlap for the same date; trend (newer) wins because it's sorted last.
    combined = combined.drop_duplicates(
        subset=["date", "code"], keep="last").reset_index(drop=True)

    if verbose:
        n_dates = combined["date"].dt.strftime("%Y-%m-%d").nunique()
        n_stocks = combined["code"].nunique()
        d0 = combined["date"].min().strftime("%Y-%m-%d")
        d1 = combined["date"].max().strftime("%Y-%m-%d")
        n_szse = combined["code"].str.endswith(".SZ").sum()
        n_sse = combined["code"].str.endswith(".SS").sum()
        n_bse = combined["code"].str.endswith(".BJ").sum()
        print(f"    [BUILD] {len(combined):,} rows | {n_stocks} stocks | "
              f"{n_dates} dates | {d0} → {d1}", flush=True)
        print(f"           SZSE (.SZ): {n_szse:,} | SSE (.SS): {n_sse:,} | BSE (.BJ): {n_bse:,}", flush=True)
        print(f"           pe non-null: {combined['pe'].notna().sum():,} | "
              f"pe>0: {(combined['pe'] > 0).sum():,}", flush=True)
        print(f"    [STATS] ok={counts['ok']} empty={counts['empty']} "
              f"total_rows={counts['rows']:,}", flush=True)
    return combined


# ============================================================================
# SSE PE file reader — separate {code}_pe.csv files in temps/sse_archive/
# ============================================================================
def _read_sse_pe_files():
    """Read all SSE PE files ({code}_pe.csv) and return a DataFrame with
    (date, code, name, pe) columns.

    SSE publishes PE in separate per-stock CSVs (quarterly snapshots + jump
    days), NOT in the daily trend CSV (whose 市盈率 column is always empty
    for SSE stocks). This reads all available PE files so they can be merged
    into the combined OHLCV DataFrame.

    The ``name`` column (证券简称) is included so that PE-only rows — dates
    that exist in the PE file but not in the trend file — can still populate
    stock_identity with a valid name.

    File schema: 日期,证券代码,证券简称,静态市盈率(倍),总换手率(%)
    """
    pe_files = glob_source_files(SSE_PE_DIR, "*_pe.csv")
    if not pe_files:
        return pd.DataFrame(columns=["date", "code", "name", "pe"])

    frames = []
    for path in pe_files:
        try:
            df = pd.read_csv(path, dtype={"证券代码": str})
        except Exception:
            continue
        if "证券代码" not in df.columns or "静态市盈率(倍)" not in df.columns:
            continue
        df = df[df["证券代码"].notna()].copy()
        df["证券代码"] = df["证券代码"].astype(str).str.strip()
        # SSE PE files store bare 6-digit codes (e.g. "600000")
        df = df[df["证券代码"].str.match(r"^\d{6}$", na=False)].copy()
        if df.empty:
            continue
        df["code"] = df["证券代码"].apply(lambda c: add_exchange_suffix(c, "上海"))
        df["pe"] = pd.to_numeric(df["静态市盈率(倍)"], errors="coerce")
        # Treat "-", empty, and 0.0 as NULL (see _read_one).
        df["pe"] = df["pe"].where(df["pe"] != 0, np.nan)
        df["date"] = pd.to_datetime(df["日期"], errors="coerce")
        df = df.dropna(subset=["date"])
        cols = ["date", "code", "pe"]
        if "证券简称" in df.columns:
            df["name"] = df["证券简称"].astype(str).str.strip()
            cols.append("name")
        df = df[cols].copy()
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["date", "code", "name", "pe"])

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["date", "code"], keep="last")
    return combined


def merge_sse_pe(combined, sse_pe, verbose=True):
    """Merge SSE PE data (from separate {code}_pe.csv files) into the
    combined OHLCV DataFrame.

    SSE trend CSVs have empty PE for all rows. This outer-joins the PE
    snapshots from {code}_pe.csv by (date, code) and fills the pe column
    where a snapshot exists. SZSE/BSE rows are unaffected (their pe is
    already populated from the trend CSV).

    An OUTER JOIN (not LEFT) is used so that PE-only rows — dates that exist
    in the PE file but NOT in the trend file (e.g. very old PE snapshots from
    2003-2005 that predate the trend file's 2020 start) — are preserved as
    rows with NULL OHLCV but valid PE. Without this, those PE values would
    be silently dropped.

    Args:
        combined: main OHLCV DataFrame with a `pe` column (NaN for SSE rows)
        sse_pe:   DataFrame with (date, code, name, pe) from _read_sse_pe_files()
        verbose:  print merge stats

    Returns:
        combined with pe filled in for SSE rows that have a PE snapshot,
        plus PE-only rows for dates not in the trend file.
    """
    if sse_pe.empty:
        if verbose:
            print("    [PE-MERGE] No SSE PE files found — SSE rows will have "
                  "NaN pe (estimated later if possible)", flush=True)
        return combined

    # Handle empty combined (e.g., all OHLCV already in DB, but PE-only rows
    # exist for dates not in any trend file). Create a DataFrame with the
    # expected columns so the OUTER JOIN produces PE-only rows.
    if combined.empty:
        combined = pd.DataFrame(columns=[
            "date", "code", "name", "code_suffix",
            "open", "high", "low", "close", "volume", "amount", "pe"
        ])

    before_pe_nonnull = combined["pe"].notna().sum() if "pe" in combined.columns else 0
    before_n_rows = len(combined)

    # Outer-join: keep both OHLCV rows and PE-only rows
    rename_map = {"pe": "pe_from_pefile"}
    if "name" in sse_pe.columns:
        rename_map["name"] = "name_from_pefile"
    merged = combined.merge(
        sse_pe.rename(columns=rename_map),
        on=["date", "code"],
        how="outer",
    )

    # Fill pe: use pe_from_pefile where pe is NULL
    merged["pe"] = merged["pe"].fillna(merged["pe_from_pefile"])
    merged = merged.drop(columns=["pe_from_pefile"])

    # For PE-only rows (no OHLCV), fill in name and code_suffix
    if "name_from_pefile" in merged.columns:
        merged["name"] = merged["name"].fillna(merged["name_from_pefile"])
        merged = merged.drop(columns=["name_from_pefile"])

    # Derive code_suffix for PE-only rows (all SSE)
    if "code_suffix" not in merged.columns:
        merged["code_suffix"] = pd.NA
    pe_only_mask = merged["code_suffix"].isna()
    if pe_only_mask.any():
        merged.loc[pe_only_mask, "code_suffix"] = merged.loc[
            pe_only_mask, "code"
        ].apply(lambda c: "SS" if str(c).endswith(".SS") else None)

    # Ensure OHLCV columns exist for PE-only rows (will be NaN)
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col not in merged.columns:
            merged[col] = pd.NA

    # Re-sort and dedupe
    merged = merged.sort_values(["date", "code"]).reset_index(drop=True)

    after_pe_nonnull = merged["pe"].notna().sum()
    n_pe_only = len(merged) - before_n_rows

    if verbose:
        n_merged = after_pe_nonnull - before_pe_nonnull
        print(f"    [PE-MERGE] SSE PE snapshots merged: +{n_merged:,} rows "
              f"with actual PE ({before_pe_nonnull:,} → {after_pe_nonnull:,})", flush=True)
        if n_pe_only > 0:
            print(f"    [PE-MERGE] +{n_pe_only} PE-only rows (dates in PE file "
                  f"but not in trend file — OHLCV will be NULL)", flush=True)
    return merged


# ============================================================================
# SSE archive per-stock trend files — historical OHLCV (the missing piece)
# ============================================================================
# The sse_trend/ directory only contains daily snapshots (a handful of recent
# dates). The FULL historical OHLCV for SSE stocks lives in per-stock
# {code}_trend.csv files under sse_archive/ (produced by download_sse_archive.py
# via the dayk endpoint). Each file contains the full listing history (from
# 2020-01-01 onwards) for one stock, with the same column schema as the
# date-grouped sse_trend_stock_*.csv files.
#
# Without loading these files, SSE stocks have only a few days of OHLCV data
# (from daily snapshots), which is far below the MIN_DAYS threshold used by
# the UI's stock list query — so NO SSE stocks appear in the UI.
def _read_sse_archive_trend_files(start_date=None, end_date=None, limit=None):
    """Read SSE per-stock {code}_trend.csv archive files (historical OHLCV).

    Globs all ``{code}_trend.csv`` files from ``temps/sse_archive/`` and reads
    each one via ``_read_one`` (same column mapping + .SS suffix addition as
    the date-grouped files). Returns a DataFrame with the same schema as
    ``build_missing_rows`` output, or an empty DataFrame if no archive files
    exist.

    Args:
        start_date: optional 'YYYY-MM-DD' lower bound (inclusive) for rows
        end_date: optional 'YYYY-MM-DD' upper bound (inclusive) for rows
        limit: optional max number of files to read (dev/test)
    """
    archive_files = glob_source_files(SSE_PE_DIR, "*_trend.csv")
    if limit:
        archive_files = archive_files[:limit]
    if not archive_files:
        return pd.DataFrame()

    sd = pd.to_datetime(start_date) if start_date else None
    ed = pd.to_datetime(end_date) if end_date else None

    frames = []
    for path in archive_files:
        df = _read_one(path, "上海")
        if df is None or df.empty:
            continue
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        if sd is not None:
            df = df[df["date"] >= sd]
        if ed is not None:
            df = df[df["date"] <= ed]
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["date", "code"], keep="last")
    return combined.reset_index(drop=True)


# ============================================================================
# PE estimation — fill missing PE from last actual PE (constant-EPS assumption)
# ============================================================================
async def estimate_missing_pe_async(conn, missing_pe_rows,
                                    history_start=None, history_end=None):
    """Estimate PE for rows where it's missing, using the last actual PE.

    For each (date, code, close), looks up the most recent row in
    stats.stock_basic_stats with actual PE (is_pe_estimated=false,
    pe IS NOT NULL, pe > 0) for the same code and an earlier date, then
    computes:

        estimated_pe = today_close * last_pe / last_close

    This assumes EPS is constant between the last PE date and the target
    date (short-term approximation). To keep the approximation valid, the
    baseline actual-PE row must be within PE_ESTIMATE_MAX_MONTHS (3 calendar
    months) of the target date; older baselines are ignored and pe stays
    NULL with is_pe_estimated=false. The lookup filters pe > 0 defensively
    (0.0 is normalized to NULL upstream, so this is a no-op there).

    The search is bounded by `history_start` / `history_end` (the date range
    of the source history): only actual-PE rows within [history_start,
    history_end] are fetched as candidate baselines. For example, if the
    source history spans 2020-today, baselines before 2020 are ignored even
    if they exist in the DB.

    Args:
        conn: asyncpg connection
        missing_pe_rows: list of (date, code, close) tuples for rows where
                         pe is NULL and needs estimation
        history_start: optional datetime.date lower bound (inclusive) for
                       baseline candidates; None = no lower bound
        history_end:   optional datetime.date upper bound (inclusive) for
                       baseline candidates; None = no upper bound

    Returns:
        Dict mapping (date, code) -> estimated_pe (float). Only includes
        rows where estimation was possible (a prior actual PE row exists
        within the 3-month window).
    """
    if not missing_pe_rows:
        return {}

    codes = sorted(set(c for (_, c, _) in missing_pe_rows))
    if not codes:
        return {}

    # Fetch all actual PE rows for the relevant codes in one query.
    # is_pe_estimated=false AND pe>0 excludes estimated rows and the
    # loss-making PE=0 marker. Bound the search to the source history's
    # date range so we don't pull in baselines from outside the window
    # being built (e.g., if history starts from 2020, don't use 2019
    # baselines from the DB).
    query = """
        SELECT code, date, pe, close
        FROM stats.stock_basic_stats
        WHERE code = ANY($1::text[])
          AND pe IS NOT NULL
          AND pe > 0
          AND close IS NOT NULL
          AND is_pe_estimated = false
    """
    params = [codes]
    if history_start is not None:
        query += "  AND date >= $2\n"
        params.append(history_start)
    if history_end is not None:
        param_idx = len(params) + 1
        query += f"  AND date <= ${param_idx}\n"
        params.append(history_end)
    query += "  ORDER BY code, date"
    rows = await conn.fetch(query, *params)

    # Build per-code sorted lists of (date, pe, close) for binary search
    pe_history = defaultdict(list)
    for r in rows:
        pe_history[r["code"]].append((r["date"], float(r["pe"]), float(r["close"])))

    # For each missing row, binary-search the last actual PE strictly before
    # the target date and apply the constant-EPS formula.
    result = {}
    for (date, code, close) in missing_pe_rows:
        history = pe_history.get(code)
        if not history:
            continue
        dates = [h[0] for h in history]
        idx = bisect.bisect_left(dates, date) - 1
        if idx < 0:
            continue  # no actual PE strictly before this date
        last_date, last_pe, last_close = history[idx]
        # Constant-EPS assumption breaks down over longer horizons — only
        # use baselines within PE_ESTIMATE_MAX_MONTHS of the target date.
        # Older baselines are skipped; pe stays NULL, is_pe_estimated=false.
        cutoff = date - relativedelta(months=PE_ESTIMATE_MAX_MONTHS)
        if last_date < cutoff:
            continue
        if last_close > 0 and close is not None and close > 0:
            estimated_pe = (close * last_pe) / last_close
            result[(date, code)] = estimated_pe

    return result


# ============================================================================
# is_in_index_or_etf computation — forward-fill (merge_asof) from sec_composition snapshots
# ============================================================================
# sec_composition ETF snapshots are sparse (quarterly-ish, 39 snapshots over
# ~4 years). A snapshot on date X applies FORWARD until the next snapshot
# (per the schema comment "applied forward via merge_asof"). So for a given
# target date D, the "active" ETF composition for each ETF is the most recent
# snapshot on or before D. A stock is_in_index_or_etf on date D if it appears in ANY
# ETF's active composition with weight_pct > 0.1.
#
# Implementation: 2 DB queries + Python binary search.
#   1. Fetch all distinct ETF snapshot dates (sorted).
#   2. Fetch all (snapshot_date, stock_code) pairs with weight_pct > 0.1.
#   3. For each target date, bisect to the most recent snapshot_date <= target,
#      then return the precomputed stock_code set for that snapshot.
# This avoids a expensive cross-join in SQL when many target dates are present.
ETF_WEIGHT_THRESHOLD = 0.1  # weight_pct > this → considered "in ETF"


async def compute_is_in_index_or_etf_async(conn, target_dates):
    """For each target date, return the set of stock codes that appear in any
    ETF's active composition (most recent snapshot on or before the date) with
    weight_pct > ETF_WEIGHT_THRESHOLD.

    Args:
        conn: asyncpg connection
        target_dates: iterable of datetime.date

    Returns:
        Dict {date: set(stock_code)}. Dates with no prior ETF snapshot map to
        an empty set. Stock codes use the same "XXXXXX.SZ/SS" format as
        stock_identity.code, so they can be compared directly.
    """
    target_dates = sorted(set(target_dates))
    if not target_dates:
        return {}

    # 1. All distinct ETF snapshot dates, sorted ascending
    snap_rows = await conn.fetch(
        "SELECT DISTINCT snapshot_date "
        "FROM stats.sec_composition "
        "WHERE source_type = 'etf' "
        "ORDER BY snapshot_date"
    )
    snap_dates = [r["snapshot_date"] for r in snap_rows]
    if not snap_dates:
        return {d: set() for d in target_dates}

    # 2. All (snapshot_date, stock_code) pairs with weight > threshold
    stock_rows = await conn.fetch(
        "SELECT snapshot_date, stock_code "
        "FROM stats.sec_composition "
        "WHERE source_type = 'etf' "
        "  AND weight_pct > $1 "
        "  AND stock_code IS NOT NULL",
        ETF_WEIGHT_THRESHOLD,
    )
    stocks_by_snap = defaultdict(set)
    for r in stock_rows:
        stocks_by_snap[r["snapshot_date"]].add(r["stock_code"])

    # 3. For each target date, bisect to the most recent snapshot on or before it
    result = {}
    for td in target_dates:
        idx = bisect.bisect_right(snap_dates, td) - 1
        if idx < 0:
            result[td] = set()  # no ETF snapshot exists on or before this date
        else:
            result[td] = stocks_by_snap.get(snap_dates[idx], set())
    return result


# ============================================================================
# Main pipeline
# ============================================================================
async def main():
    ap = argparse.ArgumentParser(
        description="Build SZSE + SSE + BSE stock OHLCV+PE and insert to database (missing dates only)."
    )
    ap.add_argument("--limit", type=int, default=None, help="Dev: first N files only")
    add_common_build_args(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "SZSE + SSE + BSE STOCK BUILDER  ·  missing-dates-only → DATABASE",
        **{
            "SZSE Archive dir": SZSE_ARCHIVE_DIR,
            "SZSE Trend dir":   SZSE_TREND_DIR,
            "SSE Trend dir":    SSE_TREND_DIR,
            "BSE Trend dir":    BSE_TREND_DIR,
            "Date range":       f"{args.start_date or '(all)'} → {args.end_date or '(all)'}",
            "Today":            TODAY_STR,
        }
    )

    # ------------------------------------------------------------------
    # 1. Discover all source CSV files in date range
    # ------------------------------------------------------------------
    print("\n[1/4] Discovering source CSV files …", flush=True)
    all_files = discover_source_files(args.start_date, args.end_date)
    if args.limit:
        all_files = all_files[:args.limit]
    print(f"    → {len(all_files)} source CSV files in range", flush=True)
    if not all_files:
        print("    [FATAL] No source CSVs found", flush=True)
        sys.exit(1)

    # Extract available dates from filenames (for missing-date detection)
    available_dates = set()
    for path, _market in all_files:
        for _dir, _pat, prefix, _mkt in SOURCE_FILE_SETS:
            ymd = ymd_from_filename(path, prefix)
            if ymd:
                d = ymd_to_date(ymd)
                if d:
                    available_dates.add(d)
                    break
    print(f"    → {len(available_dates)} unique dates available in source files", flush=True)

    # Date range of the source history — used to bound the PE-estimation
    # query so it only fetches baselines within the history window (e.g.,
    # if history starts from 2020, don't use 2019 DB baselines).
    history_start = min(available_dates) if available_dates else None
    history_end = max(available_dates) if available_dates else None
    if history_start and history_end:
        print(f"    → history date range: {history_start} → {history_end}",
              flush=True)

    # ------------------------------------------------------------------
    # 2. Connect to DB and find missing dates
    # ------------------------------------------------------------------
    print("\n[2/4] Connecting to database and detecting missing dates …", flush=True)
    conn = await get_db_or_exit()

    try:
        if args.force:
            print("    [DB] Force mode: truncating existing tables", flush=True)
            await truncate_table_async(conn, "stats.stock_basic_stats")
            await truncate_table_async(conn, "stats.stock_identity")
            missing_dates = available_dates  # all dates are missing after truncate
        else:
            missing_dates = await find_missing_dates(
                conn, "stats.stock_identity", available_dates
            )
        print(f"    [DB] {len(missing_dates)} dates missing from stats.stock_identity "
              f"(out of {len(available_dates)} available)", flush=True)

        # ------------------------------------------------------------------
        # 3. Filter source files to only missing dates and build rows
        # ------------------------------------------------------------------
        # Date-grouped source files (SZSE archive/trend + SSE trend + BSE trend)
        # are loaded only for dates missing at the DATE level. SSE historical
        # OHLCV is loaded separately from per-stock archive files below.
        combined = pd.DataFrame()
        if missing_dates:
            print(f"\n[3/4] Reading source CSVs for {len(missing_dates)} missing dates …", flush=True)
            missing_file_pairs = []
            for path, market in all_files:
                for _dir, _pat, prefix, _mkt in SOURCE_FILE_SETS:
                    ymd = ymd_from_filename(path, prefix)
                    if ymd:
                        d = ymd_to_date(ymd)
                        if d and d in missing_dates:
                            missing_file_pairs.append((path, market))
                            break

            print(f"    → {len(missing_file_pairs)} source CSV files to read", flush=True)
            combined = build_missing_rows(missing_file_pairs, verbose=True)

        # ------------------------------------------------------------------
        # 3b. Load SSE archive historical OHLCV (per-stock {code}_trend.csv)
        # ------------------------------------------------------------------
        # The sse_trend/ directory only has daily snapshots (a handful of
        # recent dates). The full historical SSE OHLCV lives in per-stock
        # {code}_trend.csv files under sse_archive/. Without loading these,
        # SSE stocks have only a few days of data — far below the MIN_DAYS
        # threshold used by the UI's stock list query, so NO SSE stocks
        # appear in the UI.
        #
        # These files are per-stock (not date-grouped), so the date-level
        # missing detection above doesn't apply. Instead, we use (date, code)-
        # level filtering: query the DB for existing SS (date, code) pairs and
        # keep only archive rows that are NOT already in the DB.
        print(f"\n    Loading SSE archive historical OHLCV from {SSE_PE_DIR} …", flush=True)
        archive_df = _read_sse_archive_trend_files(
            args.start_date, args.end_date, limit=args.limit
        )
        if len(archive_df) > 0:
            n_archive_total = len(archive_df)
            n_archive_stocks = archive_df["code"].nunique()
            d0 = archive_df["date"].min().strftime("%Y-%m-%d")
            d1 = archive_df["date"].max().strftime("%Y-%m-%d")
            print(f"    [ARCHIVE] {n_archive_total:,} rows | {n_archive_stocks} stocks | "
                  f"{d0} → {d1}", flush=True)

            if args.force:
                print(f"    [ARCHIVE] Force mode: keeping all {n_archive_total:,} rows",
                      flush=True)
            else:
                # DB-first filtering: query existing SS (date, code) pairs and
                # keep only archive rows NOT already in the DB. This avoids
                # re-inserting rows from daily snapshots already loaded.
                existing_ss_rows = await conn.fetch(
                    "SELECT date, code FROM stats.stock_identity "
                    "WHERE code_suffix = 'SS'"
                )
                if existing_ss_rows:
                    existing_df = pd.DataFrame(
                        [(r["date"], r["code"]) for r in existing_ss_rows],
                        columns=["date", "code"],
                    )
                    existing_df["date"] = pd.to_datetime(existing_df["date"])
                    # Anti-join: keep archive rows NOT in existing DB pairs
                    archive_df = archive_df.merge(
                        existing_df, on=["date", "code"], how="left", indicator=True
                    )
                    archive_df = archive_df[
                        archive_df["_merge"] == "left_only"
                    ].drop(columns=["_merge"])
                    print(f"    [ARCHIVE] {len(archive_df):,} / {n_archive_total:,} rows "
                          f"are new (skipped {n_archive_total - len(archive_df):,} existing)",
                          flush=True)
                else:
                    print(f"    [ARCHIVE] No existing SS rows in DB — loading all "
                          f"{n_archive_total:,} rows", flush=True)

            if len(archive_df) > 0:
                if len(combined) > 0:
                    combined = pd.concat([combined, archive_df], ignore_index=True)
                else:
                    combined = archive_df
                # Re-sort and dedupe (archive + date-grouped may overlap for
                # recent SSE snapshot dates — keep last = date-grouped wins)
                combined = combined.sort_values(["date", "code"]).reset_index(drop=True)
                combined = combined.drop_duplicates(
                    subset=["date", "code"], keep="last"
                ).reset_index(drop=True)
        else:
            print(f"    [ARCHIVE] No SSE archive trend files found", flush=True)

        # Expand history_start to include archive dates (SSE archive goes back
        # to 2020; date-grouped files only cover recent dates). This ensures
        # PE estimation can find baseline actual-PE rows from 2020 onwards.
        if len(combined) > 0:
            combined_min = combined["date"].min().date()
            if history_start is None or combined_min < history_start:
                history_start = combined_min
            combined_max = combined["date"].max().date()
            if history_end is None or combined_max > history_end:
                history_end = combined_max

        # Merge SSE PE data from separate {code}_pe.csv files. SSE trend CSVs
        # have empty 市盈率 for all rows; PE snapshots come from per-stock PE
        # files (quarterly + jump days). After this merge, SSE rows that have
        # a PE snapshot will have actual pe; the rest remain NaN for estimation.
        #
        # This runs BEFORE the "no new rows" check because the OUTER JOIN in
        # merge_sse_pe may produce PE-only rows (dates in PE files but not in
        # trend files) even when all OHLCV rows are already in the DB.
        print(f"\n    Merging SSE PE snapshots from {SSE_PE_DIR} …", flush=True)
        sse_pe = _read_sse_pe_files()
        combined = merge_sse_pe(combined, sse_pe, verbose=True)

        # Expand history_start/end to include PE-only dates (may go back to
        # 2003 for stocks like 600030 whose PE files predate the trend file).
        if len(combined) > 0:
            combined_min = combined["date"].min().date()
            if history_start is None or combined_min < history_start:
                history_start = combined_min
            combined_max = combined["date"].max().date()
            if history_end is None or combined_max > history_end:
                history_end = combined_max

        if len(combined) == 0:
            print("    [INFO] No new rows to insert", flush=True)
            print_wall_time(t0)
            return

        # ------------------------------------------------------------------
        # 4. Insert into database (two-pass: actual PE first, then estimated)
        # ------------------------------------------------------------------
        print(f"\n[4/4] Inserting data to database …", flush=True)

        # Convert date to datetime.date for asyncpg DATE codec.
        combined_db = combined.copy()
        combined_db["date"] = combined_db["date"].dt.date

        # Dedupe within the batch to avoid "ON CONFLICT DO UPDATE cannot
        # affect row a second time".
        combined_db = combined_db.drop_duplicates(subset=["date", "code"], keep="last")

        # Split into actual-PE and missing-PE rows. PE values of 0.0 have
        # already been normalized to NaN upstream, so they fall into the
        # missing-PE batch and may be estimated.
        actual_pe_mask = combined_db["pe"].notna()
        actual_pe_df = combined_db[actual_pe_mask]
        missing_pe_df = combined_db[~actual_pe_mask]

        n_actual = len(actual_pe_df)
        n_missing = len(missing_pe_df)
        print(f"    [BUILD] Actual PE rows: {n_actual:,} | Missing PE rows: {n_missing:,}",
              flush=True)

        # Helper: convert NaN/NA to None for asyncpg (NUMERIC columns need
        # NULL, not NaN). PE-only rows from the OUTER JOIN in merge_sse_pe
        # have NaN OHLCV — without this, asyncpg would insert NaN (not NULL)
        # and STOCK_META_SQL's "close IS NOT NULL" filter would not exclude
        # them (NaN IS NOT NULL is TRUE in PostgreSQL).
        def _to_db(v):
            if v is None or v is pd.NA:
                return None
            try:
                if isinstance(v, float) and np.isnan(v):
                    return None
            except (TypeError, ValueError):
                pass
            return v

        # --- 4a. Build & insert identity rows (all rows, FK parent) ----------
        # Compute is_in_index_or_etf: for each date in this batch, find which stocks
        # appear in any ETF's active composition (most recent snapshot on or
        # before that date) with weight_pct > 0.1. See compute_is_in_index_or_etf_async.
        batch_dates = set(combined_db["date"].tolist())
        print(f"    [ETF] Resolving is_in_index_or_etf for {len(batch_dates)} dates from "
              f"sec_composition (source_type='etf', weight_pct > {ETF_WEIGHT_THRESHOLD}) …",
              flush=True)
        etf_membership = await compute_is_in_index_or_etf_async(conn, batch_dates)

        identity_rows = []
        n_in_etf = 0
        for _, row in combined_db.iterrows():
            code = str(row["code"])
            suffix = (code.split(".")[-1]
                      if "." in code and code.split(".")[-1] in ("SZ", "SS", "BJ")
                      else None)
            in_etf = code in etf_membership.get(row["date"], set())
            if in_etf:
                n_in_etf += 1
            identity_rows.append({
                "date": row["date"],
                "code": code,
                "code_suffix": suffix,
                "name": str(row.get("name", "")) if pd.notna(row.get("name")) else "",
                "is_in_index_or_etf": in_etf,
            })
        print(f"    [ETF] {n_in_etf:,} / {len(identity_rows):,} rows flagged "
              f"is_in_index_or_etf=true in this batch", flush=True)

        if identity_rows:
            inserted = await bulk_upsert_async(
                conn, "stats.stock_identity", identity_rows, ["date", "code"]
            )
            print(f"    [DB] Inserted {inserted:,} rows into stats.stock_identity", flush=True)
        else:
            print(f"    [DB] No new rows to insert into stats.stock_identity", flush=True)

        # --- 4b. Pass 1: insert actual PE rows (is_pe_estimated=false) ------
        # Inserting these FIRST makes them visible to the estimation query
        # below, so SSE stocks with a quarterly PE snapshot earlier in this
        # batch can be used to estimate PE for later dates in the same batch.
        #
        # Split into regular rows (with OHLCV) and PE-only rows (NULL OHLCV
        # from the OUTER JOIN in merge_sse_pe). PE-only rows use a partial
        # upsert that only updates pe/is_pe_estimated, preserving any existing
        # OHLCV data in the DB.
        regular_actual_rows = []
        pe_only_actual_rows = []
        for _, row in actual_pe_df.iterrows():
            code = str(row["code"])
            close_val = _to_db(row.get("close"))
            entry = {
                "date": row["date"],
                "code": code,
                "prev_close": _to_db(row.get("prev_close")),
                "open": _to_db(row.get("open")),
                "high": _to_db(row.get("high")),
                "low": _to_db(row.get("low")),
                "close": close_val,
                "pct_change": _to_db(row.get("pct_change")),
                "pe": _to_db(row.get("pe")),
                "is_pe_estimated": False,
                "is_close_estimated": bool(row.get("is_close_estimated", False)),
            }
            if close_val is None:
                pe_only_actual_rows.append(entry)
            else:
                regular_actual_rows.append(entry)

        if regular_actual_rows:
            inserted = await bulk_upsert_async(
                conn, "stats.stock_basic_stats", regular_actual_rows, ["date", "code"]
            )
            print(f"    [DB] Inserted {inserted:,} rows into stats.stock_basic_stats "
                  f"(actual PE, is_pe_estimated=false)", flush=True)
        else:
            print(f"    [DB] No regular actual-PE rows to insert into stats.stock_basic_stats",
                  flush=True)

        if pe_only_actual_rows:
            # Partial upsert: only update pe and is_pe_estimated, preserving
            # existing OHLCV. For new rows, OHLCV columns default to NULL.
            pe_only_query = (
                "INSERT INTO stats.stock_basic_stats "
                "(date, code, pe, is_pe_estimated) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (date, code) DO UPDATE SET "
                "pe = EXCLUDED.pe, is_pe_estimated = EXCLUDED.is_pe_estimated"
            )
            pe_only_values = [
                (r["date"], r["code"], r["pe"], r["is_pe_estimated"])
                for r in pe_only_actual_rows
            ]
            async with conn.transaction():
                for i in range(0, len(pe_only_values), 1000):
                    await conn.executemany(
                        pe_only_query, pe_only_values[i:i + 1000]
                    )
            print(f"    [DB] Upserted {len(pe_only_actual_rows):,} PE-only rows "
                  f"(partial: only pe updated, OHLCV preserved if exists)", flush=True)

        # --- 4c. Pass 2: estimate missing PE and insert (is_pe_estimated=true)
        if n_missing > 0:
            # Skip PE-only rows (NULL close) — they have no OHLCV and can't be
            # estimated (estimation requires close). They're already in
            # stock_identity; no useful data to add to stock_basic_stats.
            missing_pe_df_with_close = missing_pe_df[missing_pe_df["close"].notna()].copy()
            n_pe_only_skipped = len(missing_pe_df) - len(missing_pe_df_with_close)
            if n_pe_only_skipped > 0:
                print(f"    [ESTIMATE] Skipping {n_pe_only_skipped:,} PE-only rows "
                      f"(NULL close — no estimation possible)", flush=True)

            missing_pe_tuples = []
            for _, row in missing_pe_df_with_close.iterrows():
                close_val = row.get("close")
                close_f = float(close_val) if pd.notna(close_val) else None
                missing_pe_tuples.append((row["date"], str(row["code"]), close_f))

            if missing_pe_tuples:
                print(f"    [ESTIMATE] Looking up last actual PE for {len(missing_pe_tuples):,} "
                      f"rows (history range: {history_start} → {history_end}) …", flush=True)
                estimated_pe_map = await estimate_missing_pe_async(
                    conn, missing_pe_tuples, history_start, history_end
                )
                n_estimated = len(estimated_pe_map)
                n_no_baseline = len(missing_pe_tuples) - n_estimated
                print(f"    [ESTIMATE] Estimated PE for {n_estimated:,} rows "
                      f"(is_pe_estimated=true) | {n_no_baseline:,} rows have no "
                      f"usable prior actual PE within {PE_ESTIMATE_MAX_MONTHS} "
                      f"months — pe stays NULL (is_pe_estimated=false)", flush=True)
            else:
                estimated_pe_map = {}

            estimated_basic_stats_rows = []
            for _, row in missing_pe_df_with_close.iterrows():
                code = str(row["code"])
                key = (row["date"], code)
                est_pe = estimated_pe_map.get(key)
                estimated_basic_stats_rows.append({
                    "date": row["date"],
                    "code": code,
                    "prev_close": _to_db(row.get("prev_close")),
                    "open": _to_db(row.get("open")),
                    "high": _to_db(row.get("high")),
                    "low": _to_db(row.get("low")),
                    "close": _to_db(row.get("close")),
                    "pct_change": _to_db(row.get("pct_change")),
                    "pe": est_pe,
                    # is_pe_estimated=true only when estimation succeeded;
                    # rows with no prior actual PE get NULL pe and false.
                    "is_pe_estimated": est_pe is not None,
                    "is_close_estimated": bool(row.get("is_close_estimated", False)),
                })

            if estimated_basic_stats_rows:
                inserted = await bulk_upsert_async(
                    conn, "stats.stock_basic_stats", estimated_basic_stats_rows,
                    ["date", "code"]
                )
                n_est_flag = sum(1 for r in estimated_basic_stats_rows if r["is_pe_estimated"])
                print(f"    [DB] Inserted {inserted:,} rows into stats.stock_basic_stats "
                      f"(missing-PE batch: {n_est_flag:,} estimated + "
                      f"{len(estimated_basic_stats_rows) - n_est_flag:,} NULL pe)",
                      flush=True)
        else:
            print(f"    [DB] No missing-PE rows to estimate", flush=True)

    finally:
        await conn.close()

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
