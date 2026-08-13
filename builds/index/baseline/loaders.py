"""Per-source daily history loaders → CSIndex schema.

Three loaders, each returning a list of per-code DataFrames with the same
columns as a CSIndex *_history.csv after schema normalization
(date, code, indexCode, indexName, open, high, low, close, trading_shares,
trading_amount, change, changePct, pe, consNumber):

  · load_szse_index_history  — temps/szse_archive + temps/szse_trend
                               (filters to 399001 / 399006 / 399237)
  · load_sse_index_history   — temps/sse_trend (today's EOD snapshot,
                               ~200 SSE indices, no filtering)
  · load_cnindex_history     — temps/cnindex_archive (国证 indices:
                               399303 / 399310 / 399311)

All loaders:
  • Read raw CSVs with dtype=str and encoding="utf-8-sig".
  • Apply parse_num to numeric columns and parse_date to the date column.
  • Convert source-native units to the "yuan everywhere" DB convention.
  • Add a `code` column equal to `indexCode` for grouping / existing_keys.
"""
from __future__ import annotations

import glob
import os

import pandas as pd

from _common.build_commons import parse_num, parse_date

from builds.index.baseline.paths import (
    SZSE_ARCHIVE_DIR, SZSE_TREND_DIR, SSE_TREND_DIR, CNINDEX_DIR,
    SZSE_INDEX_CODES, VALID_CODE_RE,
)


# ============================================================================
# Load SZSE index daily CSVs (archive + trend) → CSIndex schema
# ============================================================================
def load_szse_index_history(verbose: bool = True) -> list:
    """Load SZSE index daily CSVs (archive + trend) and map to CSIndex schema.

    Scans two directories for per-date index CSV files:
      • temps/szse_archive/szse_index_YYYYMMDD.csv       (historical archive)
      • temps/szse_trend/szse_trend_index_YYYYMMDD.csv   (recent trend)

    Each CSV contains ~180 indexes for one date; this function keeps only
    399001 (深证成指), 399006 (创业板指), and 399237 (运输指数) and maps
    columns to the CSIndex history schema so they can be concatenated with
    CSIndex DataFrames.

    Returns a list of per-code DataFrames. Returns an empty list if no files
    are found.
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
        "成交金额(亿元)": "trading_amount",
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

        # Filter to the indexes we want
        df["indexCode"] = df["indexCode"].astype(str).str.strip()
        df = df[df["indexCode"].isin(SZSE_INDEX_CODES)].copy()
        if len(df) == 0:
            continue

        # Parse numerics
        for col in ["prev_close", "open", "high", "low", "close", "trading_amount", "changePct"]:
            if col in df.columns:
                df[col] = df[col].apply(parse_num)
        # SZSE 成交金额(亿元) → yuan to match the "yuan everywhere" DB convention.
        if "trading_amount" in df.columns:
            df["trading_amount"] = df["trading_amount"] * 1e8  # 亿元 → yuan

        # Parse date
        df["date"] = df["date"].apply(parse_date)
        df = df.dropna(subset=["date"])

        # Compute absolute change = close - prev_close
        df["change"] = (df["close"] - df["prev_close"]).round(4)

        # Fields not provided by SZSE index data
        df["trading_shares"] = None
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
# Load SSE index trend CSVs → CSIndex schema
# ============================================================================
def load_sse_index_history(verbose: bool = True) -> list:
    """Load SSE index trend daily CSVs and map to CSIndex schema.

    Scans ``temps/sse_trend/sse_trend_index_YYYYMMDD.csv`` for per-date SSE
    index snapshot files produced by ``downloads.index.sse.trend``. Each CSV
    contains ~200 SSE indices for one date with columns 交易日期, 证券代码,
    证券简称, 前收, 开盘, 最高, 最低, 今收, 涨跌幅（%）, 成交量(万股),
    成交金额(万元), 市盈率.

    SSE trend data provides TODAY's EOD snapshot — it supplements CSIndex
    history/1m data (which may lag by a day). No code filtering is applied;
    the build's existing_keys mask handles deduplication.

    Returns a list of per-code DataFrames. Returns an empty list if no files
    are found.
    """
    trend_files = sorted(glob.glob(os.path.join(SSE_TREND_DIR, "sse_trend_index_*.csv")))

    if verbose:
        print(f"    [SSE] {len(trend_files)} trend index CSVs found", flush=True)

    if not trend_files:
        return []

    # Column rename map: SSE Chinese → CSIndex schema
    RENAME = {
        "交易日期": "date",
        "证券代码": "indexCode",
        "证券简称": "indexName",
        "前收": "prev_close",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "今收": "close",
        "涨跌幅（%）": "changePct",
        "成交量(万股)": "trading_shares",
        "成交金额(万元)": "trading_amount",
        "市盈率": "pe",
    }

    dfs = []
    for path in trend_files:
        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue

        df = df.rename(columns=RENAME)

        # Parse numerics
        for col in ["prev_close", "open", "high", "low", "close",
                     "trading_shares", "trading_amount", "changePct", "pe"]:
            if col in df.columns:
                df[col] = df[col].apply(parse_num)
        # SSE 成交量(万股) → shares; 成交金额(万元) → yuan (CSIndex uses yuan)
        if "trading_shares" in df.columns:
            df["trading_shares"] = df["trading_shares"] * 1e4  # 万股 → shares
        if "trading_amount" in df.columns:
            df["trading_amount"] = df["trading_amount"] * 1e4  # 万元 → yuan

        # Parse date
        df["date"] = df["date"].apply(parse_date)
        df = df.dropna(subset=["date"])

        # Compute absolute change = close - prev_close
        df["change"] = (df["close"] - df["prev_close"]).round(4)

        # Field not provided by SSE index data
        df["consNumber"] = None

        # code column (used by build_daily_df for grouping + existing_keys check)
        df["code"] = df["indexCode"].astype(str).str.strip()
        df["indexName"] = df["indexName"].fillna("")

        dfs.append(df)

    if not dfs:
        if verbose:
            print(f"    [SSE] No valid index data loaded", flush=True)
        return []

    combined = pd.concat(dfs, ignore_index=True)

    # Deduplicate by (date, code) — multiple trend files for the same date
    # should not happen, but guard anyway.
    combined = combined.drop_duplicates(subset=["date", "code"], keep="last")

    if verbose:
        print(f"    [SSE] loaded {len(combined)} rows across "
              f"{combined['date'].nunique()} dates, "
              f"{combined['code'].nunique()} codes "
              f"({combined['date'].min()} → {combined['date'].max()})", flush=True)

    # Return per-code DataFrames (same structure as CSIndex history files)
    return [combined[combined["code"] == code].copy() for code in combined["code"].unique()]


# ============================================================================
# Load CNINDEX (国证指数) daily CSVs → CSIndex schema
# ============================================================================
def load_cnindex_history(verbose: bool = True) -> list:
    """Load CNINDEX daily history CSVs and map to CSIndex schema.

    Scans ``temps/cnindex_archive`` for ``{code}_history.csv`` files produced
    by ``temps/convert_cnindex_xls.py`` (converted from the CNINDEX website's
    xls export).  These cover CNINDEX-published indices (国证2000 399303,
    国证A50 399310, 国证1000 399311) that are NOT available on csindex.com.cn
    or the SZSE trend endpoint.

    The CSVs already use the CSIndex history schema (date, indexCode,
    indexName, open, high, low, close, trading_shares, trading_amount, change,
    changePct, pe, consNumber), so only minimal normalization is needed:
    trading_amount is already in yuan, trading_shares already in shares.

    Returns a list of per-code DataFrames, each with a ``code`` column added.
    Returns an empty list if no files are found.
    """
    history_files = sorted(glob.glob(os.path.join(CNINDEX_DIR, "*_history.csv")))
    if verbose:
        print(f"    [CNINDEX] {len(history_files)} history CSVs in {CNINDEX_DIR}",
              flush=True)
    if not history_files:
        return []

    dfs = []
    for path in history_files:
        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue

        code = os.path.basename(path).replace("_history.csv", "")
        if not VALID_CODE_RE.match(code):
            continue
        df["code"] = code

        for col in ["open", "high", "low", "close", "trading_shares",
                     "trading_amount", "change", "changePct", "pe", "consNumber"]:
            if col in df.columns:
                df[col] = df[col].apply(parse_num)

        df["date"] = df["date"].apply(parse_date)
        df = df.dropna(subset=["date"])

        dfs.append(df)

    if not dfs:
        if verbose:
            print(f"    [CNINDEX] No valid index data loaded", flush=True)
        return []

    if verbose:
        for df in dfs:
            code = df["code"].iloc[0]
            name = df["indexName"].iloc[0] if "indexName" in df.columns and len(df) else ""
            print(f"    [CNINDEX] {code} {name}: {len(df)} dates "
                  f"({df['date'].min()} → {df['date'].max()})", flush=True)

    return dfs
