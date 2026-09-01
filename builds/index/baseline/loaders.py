"""Per-source daily history loaders → CSIndex schema.

Three loaders, each returning a list of per-code DataFrames with the same
columns as a CSIndex *_history.csv after schema normalization
(date, code, indexCode, indexName, open, high, low, close, trading_shares,
trading_amount, change, changePct, pe, consNumber):

  · load_szse_index_history  — temps/szse_archive + temps/szse_trend
                               (filters to 399001 / 399006)
  · load_sse_index_history   — temps/sse_trend (today's EOD snapshot,
                               ~200 SSE indices, no filtering)
  · load_cnindex_history     — temps/cnindex_archive (国证 indices:
                               399303 / 399310 / 399311)

All loaders:
  • Read raw CSVs via downloads.read_csv_gpu_safe (dtype=str, NO encoding
    kwarg — an `encoding=` kwarg forces a cudf CPU fallback on EVERY file;
    the utf-8-sig BOM is stripped from the first column name after read).
  • Vectorized parsing: safe_to_numeric for numeric columns and
    safe_to_datetime for the date column — NEVER Series.apply(parse_num)
    (each apply() attempt is a Numba JIT failure → one slow-path fallback
    per element under cudf.pandas).
  • Convert source-native units to the "yuan everywhere" DB convention.
  • Add a `code` column equal to `indexCode` for grouping / existing_keys.
"""
from __future__ import annotations

import glob
import os
import re
from typing import Optional

import numpy as np
import pandas as pd

from downloads._common import read_csv_gpu_safe

from builds._commons.safe_parse import safe_to_datetime, safe_to_numeric

from builds.index.baseline.paths import (
    SZSE_ARCHIVE_DIR, SZSE_TREND_DIR, SSE_TREND_DIR, CNINDEX_DIR,
    SZSE_INDEX_CODES, VALID_CODE_RE,
)

# Snapshot filenames carry their snapshot date: *_YYYYMMDD.csv
_FILE_DATE_RE = re.compile(r"_(\d{8})\.csv$")


def snapshot_file_date(path: str) -> Optional[str]:
    """'…/szse_index_20260803.csv' → '2026-08-03'; None when no date in name."""
    m = _FILE_DATE_RE.search(os.path.basename(path))
    if not m:
        return None
    ymd = m.group(1)
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"


def _fmt_date_range(series) -> str:
    """'(min → max)' date range via ONE np.asarray host transfer.

    Formatting the proxied Series scalars directly (f"{s.min()}") triggers
    a date.__format__ cudf fallback per scalar; numpy datetime64[D] str()
    is proxy-free."""
    d = np.asarray(series).astype("datetime64[D]")
    return f"{d.min()} → {d.max()}" if d.size else "(none)"


def _code_list(frame) -> list:
    """Distinct codes (appearance order) via one np.asarray host transfer.

    Series.unique() on cudf-parsed string columns falls back to CPU
    (ExtensionArrays) and iterating its result falls back per element."""
    return list(dict.fromkeys(np.asarray(frame["code"]).tolist()))


# ============================================================================
# Load SZSE index daily CSVs (archive + trend) → CSIndex schema
# ============================================================================
def load_szse_index_history(verbose: bool = True,
                            code_filter: str | None = None,
                            latest_dates: Optional[dict] = None,
                            forced_date: Optional[str] = None) -> list:
    """Load SZSE index daily CSVs (archive + trend) and map to CSIndex schema.

    Scans two directories for per-date index CSV files:
      • temps/szse_archive/szse_index_YYYYMMDD.csv       (historical archive)
      • temps/szse_trend/szse_trend_index_YYYYMMDD.csv   (recent trend)

    Each CSV contains ~180 indexes for one date; this function keeps only
    399001 (深证成指), 399006 (创业板指) and maps
    columns to the CSIndex history schema so they can be concatenated with
    CSIndex DataFrames.

    When *code_filter* is set, only rows for that one index are kept AND the
    CSV is byte-prefiltered to lines containing the code token (the 指数代码
    column carries bare 6-digit codes), so whole-market snapshots are never
    parsed during --code builds. *latest_dates* ({code: "YYYY-MM-DD"} latest
    DB date per code) lets a snapshot be skipped WITHOUT reading it when its
    filename date is already covered by every keep code — a snapshot file can
    only ever contribute the single row (file_date, code).

    *forced_date* (--date mode, "YYYY-MM-DD") bypasses the DB-covered gate
    for that date: the forced snapshot is ALWAYS read (its rows are refreshed
    through the upsert path), while other snapshots are scoped out.

    Returns a list of per-code DataFrames. Returns an empty list if no files
    are found.
    """
    # Single-code builds restricted to SZSE-published indices only; other
    # codes never appear in these files at all.
    if code_filter is not None:
        if code_filter not in SZSE_INDEX_CODES:
            return []
        keep_codes = {code_filter}
        filter_token: Optional[str] = code_filter
    else:
        keep_codes = set(SZSE_INDEX_CODES)
        filter_token = None

    archive_files = sorted(glob.glob(os.path.join(SZSE_ARCHIVE_DIR, "szse_index_*.csv")))
    trend_files = sorted(glob.glob(os.path.join(SZSE_TREND_DIR, "szse_trend_index_*.csv")))

    if verbose:
        print(f"    [SZSE] {len(archive_files)} archive + {len(trend_files)} trend "
              f"index CSVs found", flush=True)

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

    def _covered(fdate: Optional[str]) -> bool:
        """Snapshot fully covered by the DB → skip without reading."""
        if forced_date is not None:
            # --date mode: only the forced snapshot is in scope — read it
            # regardless of DB state (the missing-date skip is bypassed so
            # its rows are refreshed); other snapshots cannot contribute
            # rows that survive the single-date filter.
            return fdate is not None and fdate != forced_date
        if not fdate or not latest_dates:
            return False
        return all(
            fdate <= latest_dates[c]
            for c in keep_codes if c in latest_dates
        ) and all(c in latest_dates for c in keep_codes)

    dfs = []
    n_skipped_covered = 0
    for path in archive_files + trend_files:
        # Date-gate: snapshots whose only possible row is already in DB cost
        # nothing — no open, no read, no parse.
        fdate = snapshot_file_date(path)
        if _covered(fdate):
            n_skipped_covered += 1
            continue

        df = read_csv_gpu_safe(path, dtype=str, code=filter_token)
        if df is None or len(df) == 0:
            continue

        df = df.rename(columns=RENAME)

        _cols = np.asarray(df.columns).tolist()
        if "indexCode" not in _cols or "date" not in _cols:
            continue

        # Filter to the indexes we want (exact equality after the superset
        # byte prefilter)
        df["indexCode"] = df["indexCode"].astype(str).str.strip()
        df = df[df["indexCode"].isin(keep_codes)].copy()
        if len(df) == 0:
            continue

        # Parse numerics (vectorized — one GPU kernel per column)
        for col in ["prev_close", "open", "high", "low", "close", "trading_amount", "changePct"]:
            if col in _cols:
                df[col] = safe_to_numeric(df[col])
        # SZSE 成交金额(亿元) → yuan to match the "yuan everywhere" DB convention.
        if "trading_amount" in _cols:
            df["trading_amount"] = df["trading_amount"] * 1e8  # 亿元 → yuan

        # Parse date
        df["date"] = safe_to_datetime(df["date"])
        df = df.dropna(subset=["date"])

        # Compute absolute change = close - prev_close
        df["change"] = (df["close"] - df["prev_close"]).round(4)

        # Fields not provided by SZSE index data — np.nan keeps the columns
        # float64; None creates object dtype that breaks pd.concat against
        # the float64 CSIndex frames (MixedTypeError cascade downstream)
        df["trading_shares"] = np.nan
        df["pe"] = np.nan
        df["consNumber"] = np.nan

        # code column (used by build_daily_df for grouping + existing_keys check)
        df["code"] = df["indexCode"]

        dfs.append(df)

    if n_skipped_covered and verbose:
        print(f"    [SZSE] date-gate skipped {n_skipped_covered} snapshots "
              f"(rows already in DB)", flush=True)

    if not dfs:
        if verbose:
            print(f"    [SZSE] No valid index data loaded", flush=True)
        return []

    combined = pd.concat(dfs, ignore_index=True)

    # Deduplicate by (date, code) — trend files are appended after archive,
    # so keep="last" gives trend data priority for overlapping dates.
    combined = combined.drop_duplicates(subset=["date", "code"], keep="last")

    if verbose:
        for code in _code_list(combined):
            sub = combined[combined["code"] == code]
            name = sub["indexName"].iloc[0] if len(sub) else ""
            print(f"    [SZSE] {code} {name}: {len(sub)} dates "
                  f"({_fmt_date_range(sub['date'])})", flush=True)

    # Return per-code DataFrames (same structure as CSIndex history files)
    return [combined[combined["code"] == code].copy() for code in _code_list(combined)]


# ============================================================================
# Load SSE index trend CSVs → CSIndex schema
# ============================================================================
def load_sse_index_history(verbose: bool = True,
                           code_filter: str | None = None,
                           latest_dates: Optional[dict] = None,
                           forced_date: Optional[str] = None) -> list:
    """Load SSE index trend daily CSVs and map to CSIndex schema.

    Scans ``temps/sse_trend/sse_trend_index_YYYYMMDD.csv`` for per-date SSE
    index snapshot files produced by ``downloads.index.sse.trend``. Each CSV
    contains ~200 SSE indices for one date with columns 交易日期, 证券代码,
    证券简称, 前收, 开盘, 最高, 最低, 今收, 涨跌幅（%）, 成交量(万股),
    成交金额(万元), 市盈率.

    SSE trend data provides TODAY's EOD snapshot — it supplements CSIndex
    history/1m data (which may lag by a day).

    When *code_filter* is set, the CSV is byte-prefiltered to lines containing
    the canonical code token ("NNNNNN.SS" — the 证券代码 column carries
    suffixed codes), and *latest_dates* ({code: "YYYY-MM-DD"} latest
    DB date per code) lets a snapshot be skipped WITHOUT reading it when its
    filename date is already covered for the filtered code. *forced_date*
    (--date mode, "YYYY-MM-DD") never lets the gate skip the forced snapshot
    — its rows are refreshed through the upsert path.

    Returns a list of per-code DataFrames. Returns an empty list if no files
    are found.
    """
    # SSE snapshot files only ever carry ".SS"-suffixed codes; anything else
    # (e.g. SZSE/CNINDEX codes) cannot match any line.
    if code_filter is not None:
        filter_token: Optional[str] = f"{code_filter}.SS"
        keep_bare_codes = {code_filter}
    else:
        filter_token = None
        keep_bare_codes = None

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
    n_skipped_covered = 0
    for path in trend_files:
        # Date-gate: in single-code mode a snapshot whose only possible row
        # is already in DB is skipped without being read. Market-wide the
        # file is small (~200 rows) and always read — its rows feed both the
        # fresh tail and the estimation grid. --date mode never skips the
        # forced snapshot (missing-date skip bypassed for that date).
        fdate = snapshot_file_date(path)
        if (code_filter is not None and latest_dates
                and fdate is not None
                and latest_dates.get(code_filter) is not None
                and fdate <= latest_dates[code_filter]
                and fdate != forced_date):
            n_skipped_covered += 1
            continue

        df = read_csv_gpu_safe(path, dtype=str, code=filter_token)
        if df is None or len(df) == 0:
            continue

        df = df.rename(columns=RENAME)

        _cols = np.asarray(df.columns).tolist()
        if "indexCode" not in _cols or "date" not in _cols:
            continue

        # Parse numerics (vectorized — one GPU kernel per column)
        for col in ["prev_close", "open", "high", "low", "close",
                     "trading_shares", "trading_amount", "changePct", "pe"]:
            if col in _cols:
                df[col] = safe_to_numeric(df[col])
        # SSE 成交量(万股) → shares; 成交金额(万元) → yuan (CSIndex uses yuan)
        if "trading_shares" in _cols:
            df["trading_shares"] = df["trading_shares"] * 1e4  # 万股 → shares
        if "trading_amount" in _cols:
            df["trading_amount"] = df["trading_amount"] * 1e4  # 万元 → yuan

        # Parse date
        df["date"] = safe_to_datetime(df["date"])
        df = df.dropna(subset=["date"])

        # Compute absolute change = close - prev_close
        df["change"] = (df["close"] - df["prev_close"]).round(4)

        # Field not provided by SSE index data (float64 — see SZSE note)
        df["consNumber"] = np.nan

        # code column (used by build_daily_df for grouping + existing_keys
        # check). Canonical CSV codes carry the ".SS" suffix; the index DB
        # convention is the bare 6-digit code — strip the suffix (one
        # vectorized op, satisfies chk_index_identity_code_format).
        df["code"] = df["indexCode"].astype(str).str.split(".").str[0]
        # Exact equality after the superset byte prefilter
        if keep_bare_codes is not None:
            df = df[df["code"].isin(keep_bare_codes)].copy()
            if len(df) == 0:
                continue
        df["indexName"] = df["indexName"].fillna("")

        dfs.append(df)

    if n_skipped_covered and verbose:
        print(f"    [SSE] date-gate skipped {n_skipped_covered} snapshots "
              f"(rows already in DB)", flush=True)

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
              f"{len(_code_list(combined))} codes "
              f"({_fmt_date_range(combined['date'])})", flush=True)

    # Return per-code DataFrames (same structure as CSIndex history files)
    return [combined[combined["code"] == code].copy() for code in _code_list(combined)]


# ============================================================================
# Load CNINDEX (国证指数) daily CSVs → CSIndex schema
# ============================================================================
def load_cnindex_history(verbose: bool = True,
                         code_filter: str | None = None) -> list:
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
    # Per-code files: a single-code build only ever needs its own file.
    if code_filter is not None:
        history_files = sorted(glob.glob(
            os.path.join(CNINDEX_DIR, f"{code_filter}_history.csv")))
    else:
        history_files = sorted(glob.glob(os.path.join(CNINDEX_DIR, "*_history.csv")))
    if verbose:
        print(f"    [CNINDEX] {len(history_files)} history CSVs in {CNINDEX_DIR}",
              flush=True)
    if not history_files:
        return []

    dfs = []
    for path in history_files:
        df = read_csv_gpu_safe(path, dtype=str)
        if df is None or len(df) == 0:
            continue

        _cols = np.asarray(df.columns).tolist()

        code = os.path.basename(path).replace("_history.csv", "")
        if not VALID_CODE_RE.match(code):
            continue
        df["code"] = code

        for col in ["open", "high", "low", "close", "trading_shares",
                     "trading_amount", "change", "changePct", "pe", "consNumber"]:
            if col in _cols:
                df[col] = safe_to_numeric(df[col])

        df["date"] = safe_to_datetime(df["date"])
        df = df.dropna(subset=["date"])

        dfs.append(df)

    if not dfs:
        if verbose:
            print(f"    [CNINDEX] No valid index data loaded", flush=True)
        return []

    if verbose:
        for df in dfs:
            code = df["code"].iloc[0]
            cols = np.asarray(df.columns).tolist()
            has_name = "indexName" in cols and len(df)
            name = df["indexName"].iloc[0] if has_name else ""
            print(f"    [CNINDEX] {code} {name}: {len(df)} dates "
                  f"({_fmt_date_range(df['date'])})", flush=True)

    return dfs
