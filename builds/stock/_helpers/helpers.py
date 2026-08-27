"""GPU-safe helpers and CSV reading utilities for stock builder.

Contains:
- GPU-safe conversion helpers (_safe_to_datetime, _safe_to_numeric)
- CSV tail-peek utility (_peek_csv_max_date)
- Per-file stock CSV reader (_read_one) — reads via the canonical
  downloads._common.read_csv_gpu_safe loader
- Missing-rows builder (build_missing_rows)
- DB write helpers (_to_db, _to_db_series, _compute_eps_vec)
"""
from __future__ import annotations

import os
import io
import csv
import math
import re
from collections import Counter
from datetime import date, datetime

import numpy as np
import pandas as pd

from _common.build_commons import (
    glob_source_files,
    ymd_from_filename,
    ymd_to_date,
    select_source_files_in_range,
)
from builds._commons.column_maps import (
    STOCK_COL_MAP as COL_MAP,
    DATE_VALID_RE as _DATE_VALID_RE,
    DATE_INVALID_PATTERNS as _DATE_INVALID_PATTERNS,
)
from downloads._common import read_csv_gpu_safe

# Each entry: (directory, glob_pattern, filename_prefix, market_label, exchange_suffix)
SOURCE_FILE_SETS: list[tuple[str, str, str, str, str]] = [
    ("", "szse_stock_*.csv",        "szse_stock_",        "深圳", ".SZ"),
    ("", "szse_trend_stock_*.csv",  "szse_trend_stock_",  "深圳", ".SZ"),
    ("", "sse_trend_stock_*.csv",   "sse_trend_stock_",   "上海", ".SS"),
    ("", "bse_trend_stock_*.csv",   "bse_trend_stock_",   "北京", ".BJ"),
]


# Date formats handled by _safe_to_datetime's LEGACY fallback branch, most
# common first. Source CSVs are all zero-padded YYYY-MM-DD (guaranteed by the
# downloads conversion), so the FAST path (_safe_to_datetime) is a single
# vectorized parse with "%Y-%m-%d" and never touches any of these.
_DATE_FORMATS: tuple[tuple[str, str], ...] = (
    (r"^\d{4}-\d{1,2}-\d{1,2}$", "%Y-%m-%d"),
    (r"^\d{8}$",                  "%Y%m%d"),
    (r"^\d{4}/\d{1,2}/\d{1,2}$",  "%Y/%m/%d"),
    (r"^\d{4}\.\d{1,2}\.\d{1,2}$", "%Y.%m.%d"),
)


def _safe_to_datetime(series: pd.Series) -> pd.Series:
    """Convert a series to datetime64 — clean-data fast path first.

    downloads' CSV conversion guarantees YYYY-MM-DD dates, so the hot path
    is ONE vectorized ``pd.to_datetime(format="%Y-%m-%d")`` (GPU kernel,
    no str ops). Only when that raises (placeholder text like "没有找到…",
    foreign formats, "-" dashes) does the legacy multi-format routine run:
    invalid patterns → NA, per-format-group explicit-format parsing.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    try:
        return pd.to_datetime(series, format="%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    # --- legacy fallback: defensive multi-format parsing ------------------
    s = series.astype(str)
    s = s.str.strip()
    for pat in _DATE_INVALID_PATTERNS:
        s = s.replace(pat, pd.NA)
    s = s.replace("", pd.NA)
    s = s.replace("-", pd.NA)
    valid_mask = s.str.match(_DATE_VALID_RE, na=False)
    result = pd.Series(pd.NaT, index=series.index)
    if valid_mask.any():
        valid = s[valid_mask]
        parsed = pd.Series(pd.NaT, index=valid.index)
        remaining = pd.Series(True, index=valid.index)
        for pat, fmt in _DATE_FORMATS:
            m = remaining & valid.str.match(pat, na=False)
            if bool(m.any()):
                parsed[m] = pd.to_datetime(valid[m], format=fmt)
                remaining = remaining & ~m
        if bool(remaining.any()):
            parsed[remaining] = pd.to_datetime(valid[remaining], format="mixed")
        result[valid_mask] = parsed
    return result


def _safe_to_numeric(series: pd.Series) -> pd.Series:
    """Convert a series to float64 — clean-data fast path first.

    downloads' conversion writes plain normalized floats, so the hot path
    is a direct ``astype(float)`` (no strip/comma/regex passes). Only when
    that raises does the legacy routine run: comma thousands stripping,
    whitespace/dash/placeholder handling, regex-validated subset cast.
    """
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return series.astype(float)
    try:
        return series.astype(float)
    except (ValueError, TypeError):
        pass
    # --- legacy fallback: defensive numeric cleaning -----------------------
    s = series.astype(str)
    s = s.str.strip()
    for pat in _DATE_INVALID_PATTERNS:
        s = s.replace(pat, pd.NA)
    s = s.replace("", pd.NA)
    s = s.replace("-", pd.NA)
    s = s.str.replace(",", "", regex=False)
    valid_mask = s.str.match(r'^-?\d+\.?\d*([eE][+-]?\d+)?$', na=False)
    result = pd.Series(np.nan, index=series.index)
    if valid_mask.any():
        result[valid_mask] = s[valid_mask].astype(float)
    return result


def _safe_columns(df: pd.DataFrame) -> list[str]:
    """Materialize column names as a plain Python list.

    Thin alias for the shared `_common.df_utils.safe_columns` — avoids
    cudf fallback for `col in df.columns` (Index.__contains__ /
    Index.__len__ / IndexOpsMixin.tolist all force a slow path due to
    transfer blocking). np.asarray goes through the __array__ protocol,
    a single explicit transfer with no fallback; membership checks then
    run on CPU.
    """
    from _common.df_utils import safe_columns
    return safe_columns(df)


def _file_has_data(path: str) -> bool:
    """Cheap 2-line peek: does this CSV contain at least one data row?

    Holiday/placeholder exports are header-only or carry a single
    "没有找到符合条件的数据！" row. Such files can never produce DB rows,
    so their filename dates must be excluded from missing-date detection —
    otherwise the incremental gap check re-flags them every run and re-reads
    the same files forever (non-convergent).
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            fh.readline()  # header
            line = fh.readline()
    except OSError:
        return False
    if not line or not line.strip():
        return False  # header-only file
    return not any(mark in line for mark in ("没有找到", "无数据"))


def _peek_csv_max_date(
    path: str,
    date_col: str,
    code_col: str = "证券代码",
    tail_bytes: int = 500_000,
) -> tuple | None:
    """Peek at only the tail of a CSV file to get (max_date, first_code)
    without reading the entire file. max_date is returned as an ISO
    "YYYY-MM-DD" string.

    Per-stock CSVs (like {code}_pe.csv or {code}_trend.csv) are append-only in
    chronological order, so the max date is always near the end.

    Purely host-side (csv + re): NO pandas involved. The previous
    implementation built a small DataFrame from csv.DictReader rows — under
    cudf.pandas that constructs object-dtype columns and forces one
    to_datetime slow-path fallback PER FILE (~1,387 lines per market-wide
    run just for the incremental gate). Source CSVs guarantee YYYY-MM-DD
    dates (byte census verified across all 8,597 files), so a plain regex
    over the decoded tail is exact and allocation-free.
    """
    try:
        fsize = os.stat(path).st_size
        if fsize == 0:
            return None

        read_size = min(fsize, tail_bytes)
        with open(path, "rb") as fh:
            fh.seek(-read_size, 2)
            if fsize > read_size:
                fh.readline()  # skip first partial line to align to row boundary
            tail_data = fh.read().decode("utf-8")

        matches = re.findall(r"\d{4}-\d{2}-\d{2}", tail_data)
        if not matches:
            return None
        # lexicographic max == chronological max for zero-padded ISO dates
        max_date = max(matches)

        first_code = None
        header_end = tail_data.find("\n")
        data_text = tail_data[header_end + 1:] if header_end >= 0 else ""
        first_line = data_text.split("\n", 1)[0].strip()
        if first_line and "没有找到" not in first_line:
            cells = first_line.split(",")
            date_idx = None
            # locate the date column position so cell[0] isn't mistaken
            # for a code when schema order differs
            pos_dates = [
                i for i, c in enumerate(cells)
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", c.strip())
            ]
            if pos_dates:
                date_idx = pos_dates[-1]
            for i, c in enumerate(cells):
                token = c.strip()
                if i == date_idx or not token or \
                        re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
                    continue
                first_code = token
                break
        return (max_date, first_code)
    except Exception:
        return None


# Full read-time dtype map for canonical OHLCV CSVs. downloads' conversion
# (normalize_numeric_string + ensure_canonical_csv) guarantees numerics are
# plain floats and codes/tokens exact vocabulary — so parsing assigns final
# types directly and _safe_to_numeric becomes a no-op passthrough for every
# column here. 交易日期 is deliberately excluded (BOM'd first column name
# "\ufeff交易日期" varies between prefiltered-bytes and full reads; it is
# parsed after via the _safe_to_datetime fast path instead).
STOCK_READ_DTYPES: dict[str, str | type] = {
    "证券代码": str,
    "证券简称": str,
    "exchange": str,
    "sec_type": str,
    "board": str,
    "前收": "float64",
    "开盘": "float64",
    "最高": "float64",
    "最低": "float64",
    "今收": "float64",
    "涨跌幅（%）": "float64",
    "成交量(万股)": "float64",
    "成交金额(万元)": "float64",
    "市盈率": "float64",
}


def _dtype_for_file(path: str) -> dict[str, str | type]:
    """STOCK_READ_DTYPES entries whose columns exist in this file's header.

    Directory schemas differ (BSE/SSE trends lack e.g. 市盈率), and passing
    dtype keys absent from the file risks parser errors — one cheap header
    line read keeps the map exact per file.
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            cols = next(csv.reader([fh.readline()]))
    except (OSError, StopIteration):
        return {}
    return {c: t for c, t in STOCK_READ_DTYPES.items() if c in cols}


def _read_one(path: str, code: str | None = None) -> pd.DataFrame | None:
    """Read one canonical stock CSV, return a lean DataFrame or None.

    Source CSVs carry the canonical code schema written by downloads
    (证券代码 = "NNNNNN.XX" + exchange/board/sec_type columns — see
    downloads._common.ensure_canonical_csv). Placeholder/holiday
    exports and files without the schema return None; suffix appending
    happens at CSV-conversion time in downloads, never here.

    Reads use the full STOCK_READ_DTYPES map so parsing itself assigns
    correct types (no post-parse string coercion).

    When `code` is set, rows are filtered to that single code IMMEDIATELY
    after the raw read (before sec_type filtering / renames / numeric
    conversion) — a "WHERE code" pushed into the loader, so whole-market
    daily files never materialize in memory during --code builds.
    """
    # One-pass typed read: read_csv_gpu_safe returns an empty frame (never
    # raises) for unreadable/empty/header-only files, and the schema check
    # below turns those into None. When `code` is set the byte pre-filter
    # inside read_csv_gpu_safe shrinks each whole-market snapshot to that
    # code's lines before parsing.
    df = read_csv_gpu_safe(path, dtype=_dtype_for_file(path), code=code)
    src_cols = _safe_columns(df)
    if "证券代码" not in src_cols or "sec_type" not in src_cols:
        return None
    if code is not None:
        # canonical CSV: plain column equality on the raw frame —
        # BEFORE sec_type filtering / renames / numeric conversion
        df = df[df["证券代码"] == code]
        if df.empty:
            return None
    # canonical CSV: plain column equality, no per-row string ops
    df = df[df["sec_type"] == "stock"]
    if df.empty:
        return None
    keep = {k: v for k, v in COL_MAP.items() if k in src_cols}
    out = df[list(keep.keys())].rename(columns=keep).copy()
    # codes are already canonical "NNNNNN.XX"; carry exchange (and board)
    # through as-is (plain column selection, no suffix parsing)
    if "exchange" in src_cols:
        out["exchange"] = df["exchange"]
    if "board" in src_cols:
        out["board"] = df["board"]
    out["code"] = out["code"].astype(str)
    out_cols = _safe_columns(out)
    for c in ("prev_close", "open", "high", "low", "close", "pct_change",
              "volume_wan", "amount_wan", "pe"):
        if c in out_cols:
            out[c] = _safe_to_numeric(out[c])
    if "pe" in out_cols:
        out["pe"] = out["pe"].where(out["pe"] != 0, np.nan)
    if "volume_wan" in out_cols:
        out["trading_shares"] = out["volume_wan"] * 10000.0
        out = out.drop(columns=["volume_wan"])
    if "amount_wan" in out_cols:
        out["trading_amount"] = out["amount_wan"] * 10000.0
        out = out.drop(columns=["amount_wan"])
    return out


def discover_source_files(
    archive_dir: str,
    trend_dir: str,
    sse_trend_dir: str,
    bse_trend_dir: str,
    start_date=None,
    end_date=None,
) -> list[tuple[str, str, str]]:
    """Glob all source CSV files across the 4 directories, filtered by date range.

    Returns a list of (path, market, suffix) tuples.
    """
    sets: list[tuple[str, str, str, str, str]] = [
        (archive_dir,  "szse_stock_*.csv",        "szse_stock_",        "深圳", ".SZ"),
        (trend_dir,    "szse_trend_stock_*.csv",  "szse_trend_stock_",  "深圳", ".SZ"),
        (sse_trend_dir, "sse_trend_stock_*.csv",  "sse_trend_stock_",   "上海", ".SS"),
        (bse_trend_dir, "bse_trend_stock_*.csv",  "bse_trend_stock_",   "北京", ".BJ"),
    ]
    out: list[tuple[str, str, str]] = []
    for scan_dir, pattern, prefix, market, suffix in sets:
        files = glob_source_files(scan_dir, pattern)
        files = select_source_files_in_range(files, prefix, start_date, end_date)
        for f in files:
            out.append((f, market, suffix))
    return out


def build_missing_rows(
    file_market_pairs: list[tuple[str, str]],
    verbose: bool = True,
    code: str | None = None,
) -> pd.DataFrame:
    """Read the given source files (already filtered to missing dates),
    concatenate, dedupe by (date, code) keeping last, return a DataFrame.

    `code` pushes the row filter into each per-file read (WHERE-code in
    the loader) so whole-market files stay cheap under --code builds.
    """
    counts: Counter = Counter()
    frames: list[pd.DataFrame] = []
    for path, _market in file_market_pairs:
        df = _read_one(path, code)
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
    combined["date"] = _safe_to_datetime(combined["date"])
    combined = combined.dropna(subset=["date"])
    combined = combined.sort_values(["date", "code"]).reset_index(drop=True)
    combined = combined.drop_duplicates(
        subset=["date", "code"], keep="last").reset_index(drop=True)

    if verbose:
        # Scalar Timestamp.strftime (Series.min().strftime(...)) has no
        # cudf fast path — format on GPU then take string min/max
        # (lexicographic == chronological for YYYY-MM-DD).
        date_strs = combined["date"].dt.strftime("%Y-%m-%d")
        n_dates = date_strs.nunique()
        n_stocks = combined["code"].nunique()
        d0 = date_strs.min()
        d1 = date_strs.max()
        if "exchange" in _safe_columns(combined):
            ex = combined["exchange"].fillna("")
            n_szse = (ex == "SZ").sum()
            n_sse = (ex == "SS").sum()
            n_bse = (ex == "BJ").sum()
        else:
            n_szse = n_sse = n_bse = 0
        print(f"    [BUILD] {len(combined):,} rows | {n_stocks} stocks | "
              f"{n_dates} dates | {d0} → {d1}", flush=True)
        print(f"           SZSE: {n_szse:,} | SSE: {n_sse:,} | BSE: {n_bse:,}", flush=True)
        print(f"           pe non-null: {combined['pe'].notna().sum():,} | "
              f"pe>0: {(combined['pe'] > 0).sum():,}", flush=True)
        if "trading_shares" in _safe_columns(combined):
            print(f"           trading_shares non-null: {combined['trading_shares'].notna().sum():,} | "
                  f"trading_amount non-null: {combined['trading_amount'].notna().sum():,}", flush=True)
        print(f"    [STATS] ok={counts['ok']} empty={counts['empty']} "
              f"total_rows={counts['rows']:,}", flush=True)
    return combined


# Shared DB helper functions used by __main__.py
def _to_db(v) -> object:
    """Convert NaN/NA to None for asyncpg."""
    if v is None or v is pd.NA:
        return None
    try:
        if isinstance(v, float) and np.isnan(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _to_db_series(s: pd.Series) -> pd.Series:
    """Vectorized: convert NaN/NA in a Series to None for DB insertion."""
    return s.where(s.notna(), None)


def _nan_to_none(vals: list) -> list:
    """Post-emission NaN sweep: asyncpg writes Python float('nan') into a
    NUMERIC column as numeric-NaN (NOT NULL), breaking IS NULL checks
    downstream (poisoned estimation candidates / gap queries). pandas
    .where(cond, None) cannot reliably produce None on float dtype, so run
    this over emitted column lists — pure host-side, no cudf involvement."""
    return [
        None if isinstance(v, float) and math.isnan(v) else v for v in vals
    ]


def dates_as_date_list(series: pd.Series) -> list:
    """datetime64 column → list of Python datetime.date (one numpy transfer).

    np.asarray performs a single explicit host transfer (no cudf fallback);
    the ``[D]`` cast + object astype yields datetime.date elements that
    asyncpg accepts natively for DATE columns and that compare correctly
    with datetime.date from DB rows (bisect lookups). Never use
    Series.tolist()/itertuples/to_dict here — under cudf.pandas each
    element extraction is one slow-path fallback.
    """
    a = np.asarray(series)
    if a.size == 0:
        return []
    if a.dtype.kind == "M":  # datetime64[ns] → date objects
        return a.astype("datetime64[D]").astype(object).tolist()
    if a.dtype == object:
        out: list = []
        for v in a.tolist():
            if isinstance(v, str):
                d = date.fromisoformat(v[:10])
            elif isinstance(v, datetime):
                d = v.date()
            else:  # already a date (or unexpected — pass through)
                d = v
            out.append(d)
        return out
    raise TypeError(f"dates_as_date_list: unsupported dtype {a.dtype}")


def records_from_frame(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    """Row dicts for DB upserts WITHOUT to_dict(orient="records").

    to_dict(orient="records") under cudf.pandas emits ~1 fallback PER ROW
    (ndarray.item / ValueError on date scalars — 3,519+2,353 lines per
    market-wide run). Instead: ONE numpy array per column (single explicit
    transfer), then .tolist() converts every element to plain Python types
    in C, and row dicts are assembled by zip. Deterministic dtypes:
    float columns arrive as float, bool as bool, date columns should be
    pre-normalized via dates_as_date_list.
    """
    if not cols:
        return []
    col_lists = [_nan_to_none(np.asarray(df[c]).tolist()) for c in cols]
    return [dict(zip(cols, vals)) for vals in zip(*col_lists)]


def _compute_eps_vec(close: pd.Series, pe: pd.Series) -> pd.Series:
    """Vectorized EPS: close / pe rounded to 6 decimals, else None.

    Computes float-native (masked divide, no object-dtype intermediate —
    the old object-Series constructor triggered cudf fallbacks), then
    converts NaN→None once on the host frame (asyncpg None semantics)."""
    mask = close.notna() & pe.notna() & (pe > 0)
    vals = (close.astype(float) / pe.astype(float)).where(mask).round(6)
    return vals.astype(object).where(vals.notna(), None)
