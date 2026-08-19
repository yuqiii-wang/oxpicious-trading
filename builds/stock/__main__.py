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
import re
import sys
import time
import argparse
import bisect
import datetime
from collections import Counter, defaultdict
from pathlib import Path
from dateutil.relativedelta import relativedelta

import warnings
warnings.filterwarnings("ignore")

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import numpy as np
import pandas as pd

from downloads._common.core import add_exchange_suffix, read_csv_preferred
from _common.build_commons import (
    setup_utf8_stdout, add_common_build_args, get_db_or_exit,
    get_db_pool_async, get_max_table_date_async,
    find_missing_dates, glob_source_files, ymd_from_filename, ymd_to_date,
    filter_source_files_by_missing_dates, select_source_files_in_range,
    print_build_header, print_wall_time, PROJECT_ROOT, TODAY_STR,
    copy_or_upsert_split_async, copy_or_upsert_split_pool_async,
    truncate_table_async,
    parse_num, compute_eps,
)

setup_utf8_stdout()

import asyncio

# ============================================================================
# Paths — imported from centralized builds._commons.paths
# ============================================================================
from builds._commons.paths import (
    SZSE_ARCHIVE_DIR,
    SZSE_TREND_DIR,
    SSE_TREND_DIR,
    BSE_TREND_DIR,
    SSE_PE_DIR,
    SZSE_MARGIN_DIR,
    SSE_MARGIN_DIR,
    SZSE_ETF_PREFIXES,
    SSE_ETF_PREFIXES,
)

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
    "成交量(万股)": "volume_wan",   # raw 万股 → converted to shares below
    "成交金额(万元)": "amount_wan", # raw 万元 → converted to yuan below
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
    (SZSE_ARCHIVE_DIR, "szse_stock_*.csv",        "szse_stock_",        "深圳", ".SZ"),
    (SZSE_TREND_DIR,   "szse_trend_stock_*.csv",  "szse_trend_stock_",  "深圳", ".SZ"),
    (SSE_TREND_DIR,    "sse_trend_stock_*.csv",   "sse_trend_stock_",   "上海", ".SS"),
    (BSE_TREND_DIR,    "bse_trend_stock_*.csv",   "bse_trend_stock_",   "北京", ".BJ"),
]


def discover_source_files(start_date=None, end_date=None):
    """Glob all source CSV files across the 4 directories, filtered by date range.

    Returns a list of (path, market, suffix) tuples.
    """
    out = []
    for scan_dir, pattern, prefix, market, suffix in SOURCE_FILE_SETS:
        files = glob_source_files(scan_dir, pattern)
        files = select_source_files_in_range(files, prefix, start_date, end_date)
        for f in files:
            out.append((f, market, suffix))
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
    for c in ("prev_close", "open", "high", "low", "close", "pct_change",
              "volume_wan", "amount_wan", "pe"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    # Treat "-", empty, and 0.0 as NULL for pe: pd.to_numeric already turns
    # "-" / "" into NaN; additionally normalize 0.0 to NaN since SZSE uses
    # 市盈率=0 as a loss-making marker rather than a real PE value.
    if "pe" in out.columns:
        out["pe"] = out["pe"].where(out["pe"] != 0, np.nan)
    # Convert volume/amount from source units (万股/万元) to DB conventions
    # (trading_shares/trading_amount in yuan) to match stats.stock_basic_stats. The
    # output columns are `trading_shares` and `trading_amount` (renamed from legacy
    # `volume`/`amount`). This enables cross-table comparability — e.g.
    # analyze_industry_sentiments.py sums stock trading_amount (in yuan) to
    # derive total industry capital flow.
    if "volume_wan" in out.columns:
        out["trading_shares"] = out["volume_wan"] * 10000.0  # 万股 → shares
        out = out.drop(columns=["volume_wan"])
    if "amount_wan" in out.columns:
        out["trading_amount"] = out["amount_wan"] * 10000.0  # 万元 → yuan
        out = out.drop(columns=["amount_wan"])
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
        if "trading_shares" in combined.columns:
            print(f"           trading_shares non-null: {combined['trading_shares'].notna().sum():,} | "
                  f"trading_amount non-null: {combined['trading_amount'].notna().sum():,}", flush=True)
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
            "open", "high", "low", "close", "trading_shares", "trading_amount", "pe"
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
    for col in ["open", "high", "low", "close", "trading_shares", "trading_amount"]:
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
# Stock margin (融资融券) — read SZSE + SSE margin detail CSVs, filter to stocks
# ============================================================================
# Mirrors builds/etf/_scan_margin_dir + build_margin_df, but filters to STOCK
# codes (excludes ETF prefixes). The margin CSVs contain one row per security
# per date; for stocks we keep rows whose 6-digit code does NOT start with an
# ETF prefix (15/16 for SZSE, 510/511/512/513/515/516/518/56 for SSE).
#
# Column differences between SZSE and SSE detail CSVs:
#   SSE:  信用交易日期, 证券代码, 证券简称, 融资余额(元), 融资买入额(元),
#         融资偿还额(元), 融券余量(股/份), 融券卖出量(股/份), 融券偿还量(股/份)
#         → NO 融券余额(元), NO 融资融券余额(元)
#   SZSE: 证券代码, 证券简称, 融资买入额(元), 融资余额(元), 融券卖出量(股/份),
#         融券余量(股/份), 融券余额(元), 融资融券余额(元)
#         → NO 信用交易日期 (date from filename), NO 融资偿还额(元),
#           NO 融券偿还量(股/份)
# Missing columns are treated as 0 via parse_num(r.get(...)) — SSE stocks get
# rq_balance_amt=0 and total_balance=0; SZSE stocks get the actual values.
def _scan_stock_margin_dir(scan_dir, file_prefix, market, verbose=True, files=None):
    """Read margin detail CSVs from one directory, filtering to stock codes.

    Args:
        scan_dir: directory containing {file_prefix}*.csv files
        file_prefix: e.g. "szse_margin_detail_" or "sse_margin_detail_"
        market: "深圳" for SZSE (codes get .SZ suffix) or "上海" for SSE (.SS)
        verbose: print per-directory stats
        files: optional list of file paths (incremental mode); if None, glob all

    Returns (rows, n_files_with_data, n_empty_files).
    """
    if files is None:
        files = glob_source_files(scan_dir, f"{file_prefix}*.csv")
    else:
        files = [f for f in files if os.path.basename(f).startswith(file_prefix)]
    if verbose:
        print(f"    [STOCK-MARGIN-{market}] reading {len(files)} {file_prefix}*.csv files",
              flush=True)

    rows = []
    n_empty = 0
    n_ok = 0
    for path in files:
        ymd = ymd_from_filename(path, file_prefix)
        if not ymd:
            continue
        xlsx_path = str(Path(path).with_suffix(".xlsx"))
        try:
            df = read_csv_preferred(xlsx_path, dtype={"证券代码": str, "证券简称": str})
        except Exception:
            continue
        if df is None or len(df) == 0:
            n_empty += 1
            continue
        first_cell = str(df.iloc[0, 0]) if len(df) else ""
        if "没有找到" in first_cell or "无数据" in first_cell:
            n_empty += 1
            continue
        if "证券代码" not in df.columns:
            continue

        # Normalize code to bare 6-digit string (strip any .SS/.SZ suffix
        # already present in SSE detail CSVs).
        df["_code"] = df["证券代码"].astype(str).str.strip()
        df["_code"] = df["_code"].apply(
            lambda s: str(int(float(s))).zfill(6) if re.fullmatch(r"\d+(\.0+)?", s or "") else s
        )
        df["_code_base"] = df["_code"].str.split(".").str[0]

        # Filter to STOCK codes — exclude ETF prefixes.
        if market == "深圳":
            is_etf = df["_code_base"].str.startswith(SZSE_ETF_PREFIXES) & (df["_code_base"].str.len() == 6)
            default_suffix = ".SZ"
        else:
            is_etf = df["_code_base"].str.startswith(SSE_ETF_PREFIXES) & (df["_code_base"].str.len() == 6)
            default_suffix = ".SS"
        df = df[~is_etf].copy()
        # Also require the code to be 6-digit numeric (filters out header
        # echoes, totals rows, or other non-security rows).
        df = df[df["_code_base"].str.fullmatch(r"\d{6}")].copy()
        if len(df) == 0:
            continue

        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        # Vectorized margin row construction
        _df_out = df[["_code"]].copy()
        _df_out["code"] = np.where(
            _df_out["_code"].str.contains(r"\.", na=False),
            _df_out["_code"],
            _df_out["_code"] + default_suffix,
        )
        _df_out["date"] = date_str
        _margin_cols_src = {
            "rz_buy": "融资买入额(元)",
            "rz_balance": "融资余额(元)",
            "rq_sell_qty": "融券卖出量(股/份)",
            "rq_balance_qty": "融券余量(股/份)",
            "rq_balance_amt": "融券余额(元)",
            "total_balance": "融资融券余额(元)",
        }
        for _out_col, _src_col in _margin_cols_src.items():
            _df_out[_out_col] = df[_src_col].map(parse_num)
        rows.extend(_df_out[["date", "code"] + list(_margin_cols_src.keys())].to_dict(orient="records"))
        n_ok += 1
    if verbose:
        print(f"    [STOCK-MARGIN-{market}] {n_ok} files with data, {n_empty} empty, "
              f"{len(rows)} stock rows", flush=True)
    return rows, n_ok, n_empty


def build_stock_margin_df(verbose=True, margin_files=None):
    """Read margin CSVs from SZSE + SSE dirs and return a long DataFrame.

    Args:
        verbose: print stats
        margin_files: optional dict {"szse": [...], "sse": [...]} for incremental
                      mode (only read these files). If None, glob all files.
    """
    all_rows = []
    n_ok_total = 0
    n_empty_total = 0

    if margin_files is not None:
        if margin_files.get("szse"):
            rows_szse, ok_szse, empty_szse = _scan_stock_margin_dir(
                SZSE_MARGIN_DIR, "szse_margin_detail_", "深圳", verbose,
                files=margin_files["szse"])
            all_rows.extend(rows_szse)
            n_ok_total += ok_szse
            n_empty_total += empty_szse
        if margin_files.get("sse"):
            rows_sse, ok_sse, empty_sse = _scan_stock_margin_dir(
                SSE_MARGIN_DIR, "sse_margin_detail_", "上海", verbose,
                files=margin_files["sse"])
            all_rows.extend(rows_sse)
            n_ok_total += ok_sse
            n_empty_total += empty_sse
    else:
        if os.path.isdir(SZSE_MARGIN_DIR):
            rows_szse, ok_szse, empty_szse = _scan_stock_margin_dir(
                SZSE_MARGIN_DIR, "szse_margin_detail_", "深圳", verbose)
            all_rows.extend(rows_szse)
            n_ok_total += ok_szse
            n_empty_total += empty_szse
        if os.path.isdir(SSE_MARGIN_DIR):
            rows_sse, ok_sse, empty_sse = _scan_stock_margin_dir(
                SSE_MARGIN_DIR, "sse_margin_detail_", "上海", verbose)
            all_rows.extend(rows_sse)
            n_ok_total += ok_sse
            n_empty_total += empty_sse

    if not all_rows:
        if verbose:
            print(f"    [STOCK-MARGIN] total: {n_ok_total} files with data, "
                  f"{n_empty_total} empty, {len(all_rows)} rows", flush=True)
        return pd.DataFrame()

    out = pd.DataFrame(all_rows)
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"])

    margin_cols = ["rz_buy", "rz_balance", "rq_sell_qty", "rq_balance_qty",
                   "rq_balance_amt", "total_balance"]
    for c in margin_cols:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)

    n_before = len(out)
    # Sum duplicate (date, code) rows — a stock could appear twice in one
    # day's detail file (rare, but possible). Keep first code's identity.
    out = out.groupby(["date", "code"], as_index=False)[margin_cols].sum()
    n_after = len(out)
    n_merged = n_before - n_after

    if verbose:
        print(f"    [STOCK-MARGIN] total: {n_ok_total} files with data, "
              f"{n_empty_total} empty, {n_before} raw rows → {n_after} merged rows "
              f"({n_merged} duplicates handled)", flush=True)

    out = out.sort_values(["code", "date"]).reset_index(drop=True)
    return out


def merge_stock_margin(combined, margin_df, verbose=True):
    """Merge margin data into the combined OHLCV+PE DataFrame by (date, code).

    LEFT JOIN: every row in `combined` is preserved. Rows with no matching
    margin row (most stocks most days — only margin-eligible stocks appear in
    the margin CSVs) get 0 for all 6 margin columns. This matches the
    etf_liquidity_margin convention (NOT NULL DEFAULT 0).

    Args:
        combined: main OHLCV+PE DataFrame (must have date, code columns)
        margin_df: DataFrame from build_stock_margin_df() with margin columns
        verbose: print merge stats

    Returns:
        combined with 6 margin columns added (rz_buy, rz_balance, rq_sell_qty,
        rq_balance_qty, rq_balance_amt, total_balance), all non-null (0 when
        no margin data).
    """
    margin_cols = ["rz_buy", "rz_balance", "rq_sell_qty", "rq_balance_qty",
                   "rq_balance_amt", "total_balance"]
    if margin_df is None or margin_df.empty:
        if verbose:
            print("    [MARGIN-MERGE] No stock margin data — all margin cols set to 0",
                  flush=True)
        for c in margin_cols:
            combined[c] = 0.0
        return combined

    before_n_rows = len(combined)
    merged = combined.merge(
        margin_df[["date", "code"] + margin_cols],
        on=["date", "code"],
        how="left",
    )
    # Fill NaN margin values with 0 (NOT NULL DEFAULT 0 convention).
    for c in margin_cols:
        merged[c] = merged[c].fillna(0.0)

    n_with_margin = (merged["rz_balance"] > 0).sum()
    if verbose:
        print(f"    [MARGIN-MERGE] {n_with_margin:,} / {before_n_rows:,} rows have "
              f"non-zero rz_balance (margin-eligible stocks)", flush=True)
    return merged


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
    for path, _market, _suffix in all_files:
        for _dir, _pat, prefix, _mkt, _sfx in SOURCE_FILE_SETS:
            ymd = ymd_from_filename(path, prefix)
            if ymd:
                d = ymd_to_date(ymd)
                if d:
                    available_dates.add(d)
                    break
    print(f"    → {len(available_dates)} unique dates available in source files", flush=True)

    # Discover margin detail CSVs (SZSE + SSE). These are read ONLY for
    # dates in `missing_dates` (computed below) so we don't re-parse margin
    # files for dates already loaded. In --force mode all margin files are
    # read (stock_identity is truncated, so all dates are "missing").
    szse_margin_files_all = glob_source_files(SZSE_MARGIN_DIR, "szse_margin_detail_*.csv")
    sse_margin_files_all  = glob_source_files(SSE_MARGIN_DIR, "sse_margin_detail_*.csv")
    print(f"    → Margin: {len(szse_margin_files_all)} szse + {len(sse_margin_files_all)} sse files",
          flush=True)

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
    pool = await get_db_pool_async(min_size=1, max_size=4)

    try:
        if args.force:
            print("    [DB] Force mode: truncating existing tables", flush=True)
            # Truncate child tables first (FK order), then identity (parent).
            # truncate_table_async uses CASCADE, so truncating stock_identity
            # would cascade to all children — but explicit truncates make
            # the intent clear and avoid surprising cascade behavior.
            await truncate_table_async(conn, "stats.stock_liquidity_margin")
            await truncate_table_async(conn, "stats.stock_basic_stats")
            await truncate_table_async(conn, "stats.stock_identity")
            missing_dates = available_dates  # all dates are missing after truncate
        else:
            # Fast path: use MAX(date) instead of SELECT DISTINCT date
            # on a 4M+ row table. New dates after MAX are definitely
            # missing; gap dates (≤ max_date but absent) are rare and
            # handled by the per-suffix find_missing_dates check below.
            max_global_date = await get_max_table_date_async(
                conn, "stats.stock_identity"
            )
            if max_global_date is None:
                missing_dates = set(available_dates)
                print(f"    [DB] stats.stock_identity is empty — "
                      f"all {len(available_dates)} dates are missing", flush=True)
            else:
                new_dates = {d for d in available_dates if d > max_global_date}
                missing_dates = set(new_dates)
                n_gap_candidates = sum(
                    1 for d in available_dates if d <= max_global_date
                )
                if n_gap_candidates > 0:
                    # Check for gap dates (≤ max_date but not in DB).
                    # This is the slow path but very rare (holidays missed).
                    gap_dates = await find_missing_dates(
                        conn, "stats.stock_identity",
                        {d for d in available_dates if d <= max_global_date},
                    )
                    missing_dates |= gap_dates
                    print(f"    [DB] max existing date: {max_global_date} | "
                          f"{len(new_dates)} new + {len(gap_dates)} gap dates missing",
                          flush=True)
                else:
                    print(f"    [DB] max existing date: {max_global_date} | "
                          f"{len(new_dates)} new dates missing",
                          flush=True)

        # ------------------------------------------------------------------
        # 3. Filter source files to only missing dates and build rows
        # ------------------------------------------------------------------
        # Date-grouped source files (SZSE archive/trend + SSE trend + BSE trend)
        # are loaded only for dates missing at the DATE level. SSE historical
        # OHLCV is loaded separately from per-stock archive files below.
        #
        # CRITICAL: missing-date detection MUST be done PER-SUFFIX, not
        # globally. If SSE stocks have all dates, the global check would
        # mark no dates as missing and skip ALL SZSE/BSE files — even
        # though those suffixes have no data at all. See `find_missing_dates`
        # `code_suffix` parameter for details.
        combined = pd.DataFrame()
        if missing_dates:
            print(f"\n[3/4] Reading source CSVs for {len(missing_dates)} missing dates …", flush=True)

            # Group files by suffix for per-suffix missing-date detection
            files_by_suffix: dict[str, list[tuple]] = {".SZ": [], ".SS": [], ".BJ": []}
            for item in all_files:
                path, market, suffix = item
                files_by_suffix.setdefault(suffix, []).append((path, market))

            missing_file_pairs: list[tuple] = []
            for suffix, suffix_files in files_by_suffix.items():
                if not suffix_files:
                    continue
                # Extract dates available in this suffix's files
                suffix_dates = set()
                for path, _mkt in suffix_files:
                    for _dir, _pat, prefix, _mkt2, _sfx in SOURCE_FILE_SETS:
                        ymd = ymd_from_filename(path, prefix)
                        if ymd:
                            d = ymd_to_date(ymd)
                            if d:
                                suffix_dates.add(d)
                                break

                # Per-suffix missing-date detection — MAX(date) fast path
                max_suffix_date = await get_max_table_date_async(
                    conn, "stats.stock_identity",
                    where_clause=f"code LIKE '%%.{suffix}'"
                )
                if max_suffix_date is None:
                    suffix_missing = set(suffix_dates)
                else:
                    suffix_missing = {
                        d for d in suffix_dates if d > max_suffix_date
                    }
                    # Check for gaps within the suffix
                    gap_candidates = {
                        d for d in suffix_dates if d <= max_suffix_date
                    }
                    if gap_candidates:
                        gap_dates = await find_missing_dates(
                            conn, "stats.stock_identity", gap_candidates,
                            code_suffix=suffix,
                        )
                        suffix_missing |= gap_dates
                if not suffix_missing:
                    print(f"    [{suffix}] No dates missing (all {len(suffix_dates)} dates already loaded)", flush=True)
                    continue

                # Filter suffix files to only missing dates
                for path, market in suffix_files:
                    for _dir, _pat, prefix, _mkt, _sfx in SOURCE_FILE_SETS:
                        ymd = ymd_from_filename(path, prefix)
                        if ymd:
                            d = ymd_to_date(ymd)
                            if d and d in suffix_missing:
                                missing_file_pairs.append((path, market))
                                break

                print(f"    [{suffix}] {len(suffix_missing)} dates missing, "
                      f"{len(suffix_files)} files available, "
                      f"{sum(1 for p, _ in missing_file_pairs if any(ymd_from_filename(p, pr) for _, _, pr, _, _ in SOURCE_FILE_SETS))} files to read so far",
                      flush=True)

            # Actually count files per suffix for the summary
            total_files_to_read = len(missing_file_pairs)
            print(f"    → {total_files_to_read} source CSV files to read (all suffixes)", flush=True)
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

        # ------------------------------------------------------------------
        # 3c. Load stock margin (融资融券) from SZSE + SSE margin detail CSVs
        # ------------------------------------------------------------------
        # Margin target dates = dates that need margin data loaded:
        #   - missing_dates: new OHLCV dates being loaded (margin loaded
        #     alongside OHLCV)
        #   - missing_margin_dates: dates already in stock_liquidity_margin
        #     but with ALL margin cols = 0 (margin data not yet loaded from
        #     CSVs). This covers the post-migration case where
        #     stock_liquidity_margin was populated by copying
        #     trading_shares/trading_amount from stock_basic_stats, but the
        #     6 margin cols (rz_*, rq_*, total_balance) are still 0.
        #     Detection: GROUP BY date HAVING MAX(rz_balance)=0 AND ... —
        #     if NO stock on that date has any margin activity, the margin
        #     CSV hasn't been processed yet (SSE/SZSE detail files always
        #     contain hundreds of margin-eligible stocks with non-zero
        #     balances, so MAX=0 reliably identifies un-loaded dates).
        #
        # For the backfill case, OHLCV source CSVs are re-read for those
        # dates to recover trading_shares/trading_amount (which now live in
        # stock_liquidity_margin, not stock_basic_stats). OHLCV upsert into
        # stock_basic_stats is idempotent, so re-inserting is harmless.
        margin_available_dates = set()
        for f in szse_margin_files_all + sse_margin_files_all:
            for prefix in ("szse_margin_detail_", "sse_margin_detail_"):
                ymd = ymd_from_filename(f, prefix)
                if ymd:
                    d = ymd_to_date(ymd)
                    if d:
                        margin_available_dates.add(d)
                        break

        if args.force:
            margin_target_dates = set(missing_dates)  # = all available_dates
        else:
            # Find dates where margin/liquidity data needs backfill. Three cases:
            #
            # (a) Dates IN stock_liquidity_margin but with ALL margin cols = 0
            #     (post-migration: trading_shares/trading_amount were copied
            #     from stock_basic_stats, but the 6 margin cols are still 0).
            #     Detection: GROUP BY date HAVING MAX(rz_balance)=0 AND ... —
            #     if NO stock on that date has any margin activity, the margin
            #     CSV hasn't been processed yet.
            #
            # (b) Dates IN stock_identity but NOT in stock_liquidity_margin
            #     at all (e.g., OHLCV loaded after the migration, or dates
            #     where stock_basic_stats had NULL trading_shares/trading_amount
            #     so the migration didn't copy them). These need BOTH
            #     liquidity AND margin loaded.
            #
            # (c) Dates IN stock_liquidity_margin with margin data but
            #     trading_shares=0 AND trading_amount=0 (margin was loaded
            #     via margin-only path but OHLCV source wasn't available at
            #     the time). These need OHLCV re-read to recover liquidity.
            if margin_available_dates:
                margin_backfill_rows = await conn.fetch(
                    """
                    SELECT slm.date
                    FROM stats.stock_liquidity_margin slm
                    WHERE slm.date = ANY($1::date[])
                    GROUP BY slm.date
                    HAVING MAX(slm.rz_balance) = 0
                       AND MAX(slm.rz_buy) = 0
                       AND MAX(slm.rq_sell_qty) = 0
                       AND MAX(slm.rq_balance_qty) = 0
                       AND MAX(slm.rq_balance_amt) = 0
                       AND MAX(slm.total_balance) = 0
                    """,
                    sorted(margin_available_dates),
                )
                missing_margin_dates = {r["date"] for r in margin_backfill_rows}

                # (b) Dates in stock_identity but NOT in stock_liquidity_margin
                missing_liq_rows = await conn.fetch(
                    """
                    SELECT DISTINCT si.date
                    FROM stats.stock_identity si
                    LEFT JOIN stats.stock_liquidity_margin slm
                      ON si.date = slm.date
                    WHERE si.date = ANY($1::date[])
                      AND slm.date IS NULL
                    """,
                    sorted(margin_available_dates),
                )
                missing_liq_dates = {r["date"] for r in missing_liq_rows}

                # (c) Dates in stock_liquidity_margin with margin data but
                #     no liquidity (trading_shares=0 AND trading_amount=0).
                #     These were loaded via margin-only upsert without OHLCV.
                missing_liquidity_rows = await conn.fetch(
                    """
                    SELECT slm.date
                    FROM stats.stock_liquidity_margin slm
                    WHERE slm.date = ANY($1::date[])
                      AND slm.trading_shares = 0
                      AND slm.trading_amount = 0
                      AND slm.rz_balance > 0
                    GROUP BY slm.date
                    """,
                    sorted(margin_available_dates),
                )
                missing_liquidity_dates = {
                    r["date"] for r in missing_liquidity_rows
                }
            else:
                missing_margin_dates = set()
                missing_liq_dates = set()
                missing_liquidity_dates = set()
            margin_target_dates = (
                set(missing_dates)
                | set(missing_margin_dates)
                | set(missing_liq_dates)
                | set(missing_liquidity_dates)
            )
            if missing_margin_dates:
                print(f"    [MARGIN] {len(missing_margin_dates)} dates need "
                      f"margin backfill (all margin cols = 0 in "
                      f"stock_liquidity_margin)", flush=True)
            if missing_liq_dates:
                print(f"    [MARGIN] {len(missing_liq_dates)} dates need "
                      f"liquidity + margin (in stock_identity but NOT in "
                      f"stock_liquidity_margin)", flush=True)
            if missing_liquidity_dates:
                print(f"    [MARGIN] {len(missing_liquidity_dates)} dates need "
                      f"liquidity backfill (margin loaded but "
                      f"trading_shares/amount = 0)", flush=True)

        # If combined has no OHLCV rows (empty or PE-only with NULL close)
        # but margin backfill is needed, re-read OHLCV source CSVs for the
        # margin target dates to recover trading_shares/trading_amount.
        # Without this, margin rows would have 0 for trading_shares/
        # trading_amount (NOT NULL DEFAULT 0), losing the liquidity data
        # that belongs in stock_liquidity_margin.
        #
        # The condition checks close.notna().any() because combined may
        # contain PE-only rows (NULL OHLCV from the SSE PE merge) which
        # don't help with liquidity — we need actual OHLCV rows.
        #
        # NOTE: margin_target_dates may include dates OUTSIDE the
        # --start-date/--end-date range (e.g., old dates that need margin
        # backfill). all_files is filtered to the date range, so we glob
        # ALL source files here (unfiltered) to cover those dates.
        has_ohlcv = (
            len(combined) > 0
            and "close" in combined.columns
            and combined["close"].notna().any()
        )
        if not has_ohlcv and margin_target_dates:
            print(f"\n    [MARGIN-BACKFILL] Loading OHLCV source CSVs for "
                  f"{len(margin_target_dates)} margin target dates …",
                  flush=True)
            # Glob ALL source files (not just date-range-filtered) so we
            # can recover OHLCV for margin target dates outside the range.
            all_source_files = discover_source_files()
            backfill_file_pairs = []
            for path, market, _suffix in all_source_files:
                for _dir, _pat, prefix, _mkt, _sfx in SOURCE_FILE_SETS:
                    ymd = ymd_from_filename(path, prefix)
                    if ymd:
                        d = ymd_to_date(ymd)
                        if d and d in margin_target_dates:
                            backfill_file_pairs.append((path, market))
                            break
            print(f"    → {len(backfill_file_pairs)} source CSV files to read "
                  f"for margin backfill", flush=True)
            ohlcv_backfill = build_missing_rows(backfill_file_pairs, verbose=True)
            if len(ohlcv_backfill) > 0:
                # Concat with existing combined (may have PE-only rows).
                # Dedupe by (date, code) keeping the row with non-NULL close
                # (OHLCV rows win over PE-only rows).
                if len(combined) > 0:
                    combined = pd.concat(
                        [combined, ohlcv_backfill], ignore_index=True
                    )
                    combined["date"] = pd.to_datetime(
                        combined["date"], errors="coerce"
                    )
                    # Sort so OHLCV rows (non-NULL close) come last →
                    # drop_duplicates(keep="last") keeps them over PE-only.
                    combined = combined.sort_values(
                        ["date", "code", "close"], na_position="first"
                    ).reset_index(drop=True)
                    combined = combined.drop_duplicates(
                        subset=["date", "code"], keep="last"
                    ).reset_index(drop=True)
                else:
                    combined = ohlcv_backfill

        if len(combined) == 0 and not margin_target_dates:
            print("    [INFO] No new rows to insert", flush=True)
            print_wall_time(t0)
            return

        # Build margin DataFrame for target dates (filtered from the globbed
        # margin file lists). In --force mode, all margin files are read.
        if margin_target_dates:
            print(f"\n    Loading stock margin from SZSE + SSE detail CSVs "
                  f"({len(margin_target_dates)} target dates) …", flush=True)
            if args.force:
                margin_file_sets = {
                    "szse": szse_margin_files_all,
                    "sse":  sse_margin_files_all,
                }
            else:
                missing_szse_margin = [
                    f for f in szse_margin_files_all
                    if ymd_from_filename(f, "szse_margin_detail_")
                    and ymd_to_date(ymd_from_filename(f, "szse_margin_detail_"))
                    in margin_target_dates
                ]
                missing_sse_margin = [
                    f for f in sse_margin_files_all
                    if ymd_from_filename(f, "sse_margin_detail_")
                    and ymd_to_date(ymd_from_filename(f, "sse_margin_detail_"))
                    in margin_target_dates
                ]
                margin_file_sets = {
                    "szse": missing_szse_margin,
                    "sse":  missing_sse_margin,
                }
            margin_df = build_stock_margin_df(
                verbose=True, margin_files=margin_file_sets
            )
        else:
            margin_df = None

        # Merge margin cols into combined OHLCV+PE DataFrame (LEFT JOIN —
        # every OHLCV row is preserved; rows with no margin data get 0 for
        # all 6 margin cols, matching the NOT NULL DEFAULT 0 convention).
        combined = merge_stock_margin(combined, margin_df, verbose=True)

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

        # Vectorized: convert NaN/NA in a Series to None for DB insertion.
        def _to_db_series(s: pd.Series) -> pd.Series:
            return s.where(s.notna(), None)

        # Vectorized EPS: close / pe rounded to 6 decimals, else None.
        def _compute_eps_vec(close: pd.Series, pe: pd.Series) -> pd.Series:
            mask = close.notna() & pe.notna() & (pe > 0)
            vals = (close[mask].astype(float) / pe[mask].astype(float)).round(6)
            result = pd.Series([None] * len(close), index=close.index, dtype=object)
            result[mask] = vals
            return result

        # --- 4a. Build & insert identity rows (all rows, FK parent) ----------
        # Compute is_in_index_or_etf: for each date in this batch, find which stocks
        # appear in any ETF's active composition (most recent snapshot on or
        # before that date) with weight_pct > 0.1. See compute_is_in_index_or_etf_async.
        batch_dates = set(combined_db["date"].tolist())
        print(f"    [ETF] Resolving is_in_index_or_etf for {len(batch_dates)} dates from "
              f"sec_composition (source_type='etf', weight_pct > {ETF_WEIGHT_THRESHOLD}) …",
              flush=True)
        etf_membership = await compute_is_in_index_or_etf_async(conn, batch_dates)

        # Vectorized identity rows construction
        _id_df = combined_db[["date", "code", "name"]].copy()
        _id_df["code"] = _id_df["code"].astype(str)
        # Extract suffix: last part after "." if it's a known exchange suffix
        _id_df["code_suffix"] = np.where(
            _id_df["code"].str.contains(r"\.", na=False),
            _id_df["code"].str.split(".").str[-1],
            "",
        )
        _id_df["code_suffix"] = _id_df["code_suffix"].where(
            _id_df["code_suffix"].isin(["SZ", "SS", "BJ"]), ""
        )
        # Name: convert NaN to empty string
        _id_df["name"] = _id_df["name"].where(_id_df["name"].notna(), "")
        _id_df["name"] = _id_df["name"].astype(str)
        # Vectorized ETF membership: build a lookup DataFrame and merge
        _etf_rows: list[tuple] = []
        for _d, _codes in etf_membership.items():
            _etf_rows.extend((_d, _c) for _c in _codes)
        _etf_df = pd.DataFrame(_etf_rows, columns=["date", "code"]).drop_duplicates() if _etf_rows else pd.DataFrame(columns=["date", "code"])
        if not _etf_df.empty:
            _etf_df["is_in_index_or_etf"] = True
            _id_df = _id_df.merge(_etf_df, on=["date", "code"], how="left")
        else:
            _id_df["is_in_index_or_etf"] = False
        _id_df["is_in_index_or_etf"] = _id_df["is_in_index_or_etf"].fillna(False).astype(bool)
        n_in_etf = int(_id_df["is_in_index_or_etf"].sum())
        identity_rows = _id_df[["date", "code", "code_suffix", "name", "is_in_index_or_etf"]].to_dict(orient="records")
        print(f"    [ETF] {n_in_etf:,} / {len(identity_rows):,} rows flagged "
              f"is_in_index_or_etf=true in this batch", flush=True)

        if identity_rows:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, "stats.stock_identity", identity_rows, ["date", "code"]
            )
            total_inserted = n_copied + n_upserted
            if n_copied > 0 and n_upserted == 0:
                print(f"    [DB] Inserted {total_inserted:,} rows into "
                      f"stats.stock_identity via COPY (all new dates)",
                      flush=True)
            elif n_copied > 0:
                print(f"    [DB] Inserted {total_inserted:,} rows into "
                      f"stats.stock_identity ({n_copied:,} copied + "
                      f"{n_upserted:,} upserted)", flush=True)
            else:
                print(f"    [DB] Inserted {total_inserted:,} rows into "
                      f"stats.stock_identity via upsert (all historical)",
                      flush=True)
        else:
            print(f"    [DB] No new rows to insert into stats.stock_identity", flush=True)

        # --- Build ALL row lists first (computation only, no DB writes) ---
        # This allows the independent table writes (basic_stats vs
        # liquidity_margin) to run in parallel via the connection pool.

        # --- 4b-row. Build regular + PE-only actual PE rows (vectorized) ---------
        _pe_cols = ["prev_close", "open", "high", "low", "close", "pct_change", "pe"]
        _pe_df = actual_pe_df[["date", "code"] + _pe_cols + ["is_close_estimated"]].copy()
        _pe_df["code"] = _pe_df["code"].astype(str)
        # Vectorized NaN→None conversion for all numeric columns
        for _c in _pe_cols:
            _pe_df[_c] = _to_db_series(_pe_df[_c])
        # Vectorized EPS computation
        _pe_df["eps"] = _compute_eps_vec(_pe_df["close"], _pe_df["pe"])
        _pe_df["is_pe_estimated"] = False
        _pe_df["is_close_estimated"] = _pe_df["is_close_estimated"].fillna(False).astype(bool)
        # Split: close is None → pe-only, else regular
        _is_pe_only = _pe_df["close"].isna()
        regular_actual_rows = _pe_df[~_is_pe_only].to_dict(orient="records")
        pe_only_actual_rows = _pe_df[_is_pe_only].to_dict(orient="records")

        # --- 4d-row. Build liq_rows + margin_only_rows -------------
        # These are independent of basic_stats — build them now so the
        # writes can run in parallel with basic_stats writes.

        # (1) FULL rows from combined_db (OHLCV + all 8 cols) — vectorized
        _liq_cols = ["trading_shares", "trading_amount", "rz_buy", "rz_balance",
                      "rq_sell_qty", "rq_balance_qty", "rq_balance_amt", "total_balance"]
        _liq_df = combined_db[["date", "code", "close"] + _liq_cols].copy()
        _liq_df["code"] = _liq_df["code"].astype(str)
        # Filter out PE-only rows (close is None/NaN)
        _has_close = _liq_df["close"].notna()
        _liq_df = _liq_df[_has_close].drop(columns=["close"])
        # Vectorized NaN→None→0 for all numeric columns
        for _c in _liq_cols:
            _liq_df[_c] = _to_db_series(_liq_df[_c]).fillna(0)
        liq_rows = _liq_df.to_dict(orient="records")

        # (2) MARGIN-ONLY rows from margin_df (6 margin cols only) — vectorized
        # Build ohlcv_keys from the liquidity DataFrame (before dict conversion)
        _liq_keys = _liq_df[["date", "code"]].drop_duplicates()
        ohlcv_keys: set[tuple] = set(zip(_liq_keys["date"], _liq_keys["code"]))
        margin_only_rows: list[dict] = []
        if margin_df is not None and len(margin_df) > 0:
            margin_df_db = margin_df.copy()
            margin_df_db["date"] = pd.to_datetime(margin_df_db["date"]).dt.date
            _margin_cols = ["rz_buy", "rz_balance", "rq_sell_qty", "rq_balance_qty",
                            "rq_balance_amt", "total_balance"]
            _mdf = margin_df_db[["date", "code"] + _margin_cols].copy()
            _mdf["code"] = _mdf["code"].astype(str)
            # Filter out rows already in ohlcv_keys using anti-join
            _mdf = _mdf.merge(_liq_keys, on=["date", "code"], how="left", indicator=True)
            _mdf = _mdf[_mdf["_merge"] == "left_only"].drop(columns=["_merge"])
            # Vectorized NaN→None→0 for margin columns
            for _c in _margin_cols:
                _mdf[_c] = _to_db_series(_mdf[_c]).fillna(0)
            margin_only_rows = _mdf.to_dict(orient="records")

        # --- Parallel DB writes: basic_stats + liquidity_margin -----
        async def _write_basic_stats():
            """Write regular + pe_only to basic_stats, then estimated."""
            async with pool.acquire() as bs_conn:
                # Pass 1: regular actual PE rows
                if regular_actual_rows:
                    n_copied, n_upserted = await copy_or_upsert_split_async(
                        bs_conn, "stats.stock_basic_stats", regular_actual_rows,
                        ["date", "code"]
                    )
                    total = n_copied + n_upserted
                    via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                          f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
                          "upsert"
                    print(f"    [DB] Inserted {total:,} rows into stats.stock_basic_stats "
                          f"(actual PE, is_pe_estimated=false) via {via}", flush=True)
                else:
                    print(f"    [DB] No regular actual-PE rows to insert into stats.stock_basic_stats",
                          flush=True)

                # PE-only rows (partial upsert)
                if pe_only_actual_rows:
                    pe_only_query = (
                        "INSERT INTO stats.stock_basic_stats "
                        "(date, code, pe, is_pe_estimated) "
                        "VALUES ($1, $2, $3, $4) "
                        "ON CONFLICT (date, code) DO UPDATE SET "
                        "pe = EXCLUDED.pe, "
                        "is_pe_estimated = EXCLUDED.is_pe_estimated, "
                        "eps = CASE WHEN stats.stock_basic_stats.close IS NOT NULL "
                        "            AND EXCLUDED.pe > 0 "
                        "       THEN stats.stock_basic_stats.close / EXCLUDED.pe "
                        "       ELSE NULL END"
                    )
                    pe_only_values = [
                        (r["date"], r["code"], r["pe"], r["is_pe_estimated"])
                        for r in pe_only_actual_rows
                    ]
                    async with bs_conn.transaction():
                        for i in range(0, len(pe_only_values), 1000):
                            await bs_conn.executemany(
                                pe_only_query, pe_only_values[i:i + 1000]
                            )
                    print(f"    [DB] Upserted {len(pe_only_actual_rows):,} PE-only rows "
                          f"(partial: only pe updated, OHLCV preserved if exists)", flush=True)

        async def _write_liquidity_margin():
            """Write liq_rows + margin_only_rows to stock_liquidity_margin."""
            async with pool.acquire() as lm_conn:
                # (1) FULL rows
                if liq_rows:
                    n_copied, n_upserted = await copy_or_upsert_split_async(
                        lm_conn, "stats.stock_liquidity_margin", liq_rows, ["date", "code"]
                    )
                    total = n_copied + n_upserted
                    n_with_margin = sum(
                        1 for r in liq_rows if (r.get("rz_balance") or 0) > 0
                    )
                    via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                          f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
                          "upsert"
                    print(f"    [DB] Inserted {total:,} rows into "
                          f"stats.stock_liquidity_margin ({n_with_margin:,} with "
                          f"non-zero rz_balance) via {via}", flush=True)
                else:
                    print(f"    [DB] No OHLCV rows to insert into "
                          f"stats.stock_liquidity_margin", flush=True)

                # (2) MARGIN-ONLY backfill (temp table + JOIN identity for FK safety)
                if margin_only_rows:
                    print(f"    [DB] Preparing {len(margin_only_rows):,} "
                          f"margin-only rows for upsert (FK filtering via "
                          f"temp table)…", flush=True)
                    async with lm_conn.transaction():
                        await lm_conn.execute(
                            "CREATE TEMP TABLE _margin_upsert ("
                            "  date DATE, code TEXT, "
                            "  rz_buy NUMERIC(24,4), rz_balance NUMERIC(24,4), "
                            "  rq_sell_qty NUMERIC(24,4), "
                            "  rq_balance_qty NUMERIC(24,4), "
                            "  rq_balance_amt NUMERIC(24,4), "
                            "  total_balance NUMERIC(24,4)"
                            ") ON COMMIT DROP"
                        )
                        temp_values = [
                            (r["date"], r["code"],
                             r["rz_buy"], r["rz_balance"],
                             r["rq_sell_qty"], r["rq_balance_qty"],
                             r["rq_balance_amt"], r["total_balance"])
                            for r in margin_only_rows
                        ]
                        insert_query = (
                            "INSERT INTO _margin_upsert "
                            "(date, code, rz_buy, rz_balance, rq_sell_qty, "
                            " rq_balance_qty, rq_balance_amt, total_balance) "
                            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
                        )
                        for i in range(0, len(temp_values), 1000):
                            await lm_conn.executemany(
                                insert_query, temp_values[i:i + 1000]
                            )
                        result = await lm_conn.execute(
                            "INSERT INTO stats.stock_liquidity_margin "
                            "(date, code, rz_buy, rz_balance, rq_sell_qty, "
                            " rq_balance_qty, rq_balance_amt, total_balance) "
                            "SELECT t.date, t.code, t.rz_buy, t.rz_balance, "
                            "       t.rq_sell_qty, t.rq_balance_qty, "
                            "       t.rq_balance_amt, t.total_balance "
                            "FROM _margin_upsert t "
                            "INNER JOIN stats.stock_identity si "
                            "  ON si.date = t.date AND si.code = t.code "
                            "ON CONFLICT (date, code) DO UPDATE SET "
                            "  rz_buy = EXCLUDED.rz_buy, "
                            "  rz_balance = EXCLUDED.rz_balance, "
                            "  rq_sell_qty = EXCLUDED.rq_sell_qty, "
                            "  rq_balance_qty = EXCLUDED.rq_balance_qty, "
                            "  rq_balance_amt = EXCLUDED.rq_balance_amt, "
                            "  total_balance = EXCLUDED.total_balance"
                        )
                        parts = result.split()
                        inserted_count = int(parts[-1]) if parts else 0

                    n_with_margin = sum(
                        1 for r in margin_only_rows
                        if (r.get("rz_balance") or 0) > 0
                    )
                    print(f"    [DB] Upserted {inserted_count:,} margin-only "
                          f"rows into stats.stock_liquidity_margin (6 margin "
                          f"cols, trading_shares/amount preserved; "
                          f"{n_with_margin:,} with non-zero rz_balance)",
                          flush=True)
                else:
                    print(f"    [DB] No margin-only backfill rows to insert "
                          f"(all margin rows already covered by OHLCV insert)",
                          flush=True)

        # Phase 1: run basic_stats (regular+pe_only) and liquidity_margin in parallel
        print(f"\n    [POOL] Running basic_stats + liquidity_margin writes in parallel "
              f"(pool size={pool.size}) …", flush=True)
        await asyncio.gather(
            _write_basic_stats(),
            _write_liquidity_margin(),
        )

        # --- 4c. Pass 2: estimate missing PE and insert (is_pe_estimated=true)
        # This MUST run AFTER basic_stats is inserted (the estimation uses
        # the current batch's basic_stats rows as PE reference).
        if n_missing > 0:
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
            if not missing_pe_df_with_close.empty:
                _est_cols = ["prev_close", "open", "high", "low", "close", "pct_change"]
                _est_df = missing_pe_df_with_close[["date", "code"] + _est_cols + ["is_close_estimated"]].copy()
                _est_df["code"] = _est_df["code"].astype(str)
                # Vectorized NaN→None conversion
                for _c in _est_cols:
                    _est_df[_c] = _to_db_series(_est_df[_c])
                # Map estimated PE values from the dict
                _pe_keys: list[tuple] = list(zip(_est_df["date"], _est_df["code"]))
                _est_pe_vals: list = [estimated_pe_map.get(k) for k in _pe_keys]
                _est_df["pe"] = _est_pe_vals
                # Vectorized EPS computation
                _est_df["eps"] = _compute_eps_vec(_est_df["close"], _est_df["pe"].astype(float))
                _est_df["is_pe_estimated"] = _est_df["pe"].notna()
                _est_df["is_close_estimated"] = _est_df["is_close_estimated"].fillna(False).astype(bool)
                estimated_basic_stats_rows = _est_df[["date", "code"] + _est_cols + ["pe", "eps", "is_pe_estimated", "is_close_estimated"]].to_dict(orient="records")

            if estimated_basic_stats_rows:
                n_copied, n_upserted = await copy_or_upsert_split_async(
                    conn, "stats.stock_basic_stats", estimated_basic_stats_rows,
                    ["date", "code"]
                )
                total = n_copied + n_upserted
                n_est_flag = sum(1 for r in estimated_basic_stats_rows if r["is_pe_estimated"])
                via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                      f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
                      "upsert"
                print(f"    [DB] Inserted {total:,} rows into stats.stock_basic_stats "
                      f"(missing-PE batch: {n_est_flag:,} estimated + "
                      f"{len(estimated_basic_stats_rows) - n_est_flag:,} NULL pe) via {via}",
                      flush=True)
        else:
            print(f"    [DB] No missing-PE rows to estimate", flush=True)

        # ------------------------------------------------------------------
        # 5. Compute tech stats (MA/EMA) for all stocks
        # ------------------------------------------------------------------
        # Run tech_stats as a final phase — computes MA5/20/60/120/255 and
        # EMA6/10/20/60/120/255 from stock_basic_stats.close. This is
        # integrated here so builds.stock can produce both OHLCV+PE+margin
        # and technical indicators in one pass.
        print("\n[5/5] Computing stock tech stats (MA/EMA) …", flush=True)
        from builds.stock.tech_stats import run_tech_stats_chunked

        tech_stats_force = args.force
        tech_total = await run_tech_stats_chunked(
            conn, force=tech_stats_force, chunk_size=500
        )
        print(f"    [TECH-STATS] Total rows upserted into stats.stock_tech_stats: "
              f"{tech_total:,}", flush=True)

    finally:
        await conn.close()
        await pool.close()

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
