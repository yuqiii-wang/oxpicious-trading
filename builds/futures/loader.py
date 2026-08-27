"""builds.futures.loader — CSV reading, parsing, and row construction for CFFEX futures.

Reads per-day futures CSV files from temps/cffex_archive/YYYYMM/YYYYMMDD_futures.csv
and produces two DataFrames:
  - identity_df:    (date, code, product_code, contract_month, ...)
  - basic_stats_df:  (date, code, open, high, low, close, ...)

The output follows the same pattern as builds/stock and builds/options:
read CSV → parse columns → build identity + basic_stats → bulk upsert into DB.
"""
from __future__ import annotations

import os
import re
from datetime import date as _date
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

# Epoch anchor for vectorized days_to_expiry math (no date objects in frames)
_EPOCH = _date(1970, 1, 1)

from builds.futures.config import (
    COL_MAP,
    PRODUCT_NAMES,
    PRODUCT_TYPES,
    PRODUCT_UNDERLYING,
    compute_expiry_date,
    normalize_contract_year_month,
    parse_contract_code,
)
from builds.futures.paths import CFFEX_ARCHIVE_DIR, FUTURES_CSV_PATTERN
from downloads._common import read_csv_gpu_safe

# Regex to extract YYYYMMDD from filename like "20260701_futures.csv"
_FILENAME_DATE_RE = re.compile(r"(\d{8})")


def ymd_from_futures_filename(filepath: str | os.PathLike) -> Optional[str]:
    """Extract YYYYMMDD from a futures CSV filename.

    Args:
        filepath: path like "temps/cffex_archive/202607/20260701_futures.csv"

    Returns:
        "20260701" or None if not parseable.
    """
    basename = os.path.basename(str(filepath))
    m = _FILENAME_DATE_RE.search(basename)
    if not m:
        return None
    return m.group(1)


def ymd_to_date(ymd: str) -> Optional[pd.Timestamp]:
    """Convert YYYYMMDD string to pandas Timestamp.

    Returns None on invalid input.
    """
    try:
        return pd.to_datetime(ymd, format="%Y%m%d")
    except Exception:
        return None


def glob_futures_files(archive_dir: str = CFFEX_ARCHIVE_DIR) -> List[str]:
    """Glob all *_futures.csv files under the CFFEX archive directory.

    Searches recursively (YYYYMM subdirectories) and returns sorted file paths.
    """
    result: List[str] = []
    for root, _dirs, files in os.walk(archive_dir):
        for fname in files:
            if fname.endswith("_futures.csv"):
                result.append(os.path.join(root, fname))
    return sorted(result)


def filter_files_by_dates(
    files: List[str],
    target_dates: set[pd.Timestamp],
) -> List[str]:
    """Filter futures CSV files to only those whose date is in target_dates.

    Fully vectorized: the YYYYMMDD token is regex-extracted from ALL paths
    in ONE Series.str pass and matched against the target set via isin —
    no per-file Python loop, no list append; kept paths go straight from
    the boolean mask to a numpy ``.tolist()``.
    """
    if not files or not target_dates:
        return []
    # target dates → YYYYMMDD strings without a Python loop (datetime64 →
    # "YYYY-MM-DD" → strip "-")
    d64 = np.asarray(sorted(target_dates), dtype="datetime64[D]")
    target_ymd = set(np.char.replace(d64.astype("U10"), "-", ""))

    paths = pd.Series(files, dtype="object")
    # (\d{8})_futures.csv anchored at the end — separator-agnostic and
    # equivalent to ymd_from_futures_filename (stem must be exactly 8 digits)
    ymd = paths.str.extract(r"(\d{8})_futures\.csv$", expand=False)
    keep = ymd.notna() & ymd.isin(target_ymd)
    return np.asarray(paths[keep], dtype=object).tolist()


def _read_one_csv(filepath: str) -> Optional[pd.DataFrame]:
    """Read a single futures CSV file and return a clean DataFrame.

    Source CSVs are generated canonical by downloads (whitespace-free cells,
    null tokens already ""), so the read is PLAIN — no dtype argument, no
    post-parse coercion. pandas auto-inference lands every column on its
    final type (str contract ids, float64 numerics); a column that cannot
    be inferred cleanly is a downloads bug, fixed at the generator.

    Returns DataFrame or None if file is empty/unreadable.
    """
    try:
        df = read_csv_gpu_safe(filepath)
    except Exception:
        return None

    if df is None or len(df) == 0:
        return None

    # Check for required column
    if "合约代码" not in np.asarray(df.columns).tolist():
        return None

    # Filter out rows with empty contract codes (one vectorized str op)
    df = df[df["合约代码"].astype(str).str.len() > 0]
    if df.empty:
        return None

    # Rename columns
    return df.rename(columns=COL_MAP)


def build_futures_df(
    files: List[str],
    verbose: bool = True,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Build identity and basic_stats DataFrames from a list of futures CSV files.

    Row construction is fully vectorized: per-file frames carry a scalar
    ``date`` broadcast and are concatenated ONCE; contract attributes are
    resolved by parsing each UNIQUE code exactly once (host-side) into a
    small lookup frame that is merged back on ``code`` — no iterrows /
    per-row dict loops (each element extraction is a cudf.pandas
    slow-path fallback).

    Args:
        files: list of *_futures.csv file paths to read
        verbose: print progress messages

    Returns:
        (identity_df, basic_stats_df) — two DataFrames ready for DB insertion.
        identity_df columns: date, code, product_code, contract_month,
                             contract_year_month, contract_type, name,
                             underlying_code, underlying_name,
                             days_to_expiry
        basic_stats_df columns: date, code, open, high, low, close,
                               settlement_price, prev_settlement, change,
                               change_pct, trading_shares, trading_amount,
                               open_interest, open_interest_change, delta
    """
    if verbose:
        print(f"    [FUTURES] reading {len(files)} *_futures.csv files", flush=True)

    frames: list[pd.DataFrame] = []
    n_empty = 0
    n_ok = 0

    for filepath in files:
        ymd = ymd_from_futures_filename(filepath)
        if not ymd:
            continue
        date_ts = ymd_to_date(ymd)
        if date_ts is None:
            continue

        df = _read_one_csv(filepath)
        if df is None or df.empty:
            n_empty += 1
            continue

        # Scalar broadcast: datetime64 column (never object date lists)
        df["date"] = pd.Timestamp(date_ts.date())
        frames.append(df)
        n_ok += 1

    identity_cols = [
        "date", "code", "product_code", "contract_month",
        "contract_year_month", "contract_type", "name",
        "underlying_code", "underlying_name", "days_to_expiry",
    ]
    basic_cols = [
        "date", "code", "open", "high", "low", "close",
        "settlement_price", "prev_settlement", "change", "change_pct",
        "trading_shares", "trading_amount",
        "open_interest", "open_interest_change", "delta",
    ]
    if not frames:
        return pd.DataFrame(columns=identity_cols), pd.DataFrame(columns=basic_cols)

    all_df = pd.concat(frames, ignore_index=True)

    # Parse each UNIQUE contract code exactly once (host-side); invalid
    # codes are dropped via the inner merge (counted as parse failures).
    uniq_codes = sorted(set(np.asarray(all_df["code"]).tolist()))
    meta_rows: list[dict] = []
    n_invalid_contracts = 0
    for c in uniq_codes:
        try:
            product_code, contract_month = parse_contract_code(c)
        except ValueError:
            n_invalid_contracts += 1
            continue
        contract_type = PRODUCT_TYPES.get(product_code, "unknown")
        expiry_date = compute_expiry_date(contract_month, contract_type)
        meta_rows.append({
            "code": c,
            "product_code": product_code,
            "contract_month": contract_month,
            "contract_year_month": normalize_contract_year_month(contract_month),
            "contract_type": contract_type,
            "name": PRODUCT_NAMES.get(product_code, product_code),
            "underlying_code": PRODUCT_UNDERLYING.get(product_code, ("", ""))[0],
            "underlying_name": PRODUCT_UNDERLYING.get(product_code, ("", ""))[1],
            "_exp_days": (expiry_date - _EPOCH).days,
        })
    if not meta_rows:
        if verbose:
            print(
                f"    [FUTURES] {n_ok} files with data, {n_empty} empty, "
                f"all {n_invalid_contracts} contracts invalid — no rows",
                flush=True,
            )
        return pd.DataFrame(columns=identity_cols), pd.DataFrame(columns=basic_cols)

    # Inner merge attaches parsed attrs + drops rows whose code is invalid
    merged_df = all_df.merge(pd.DataFrame(meta_rows), on="code", how="inner")
    n_parse_fail = len(all_df) - len(merged_df)

    # days_to_expiry: expiry epoch-days (per-contract constant) minus the
    # row's trade-date epoch days, clamped at 0 — fully vectorized, no
    # python-date objects inside any DataFrame.
    d_epoch = (
        pd.to_datetime(merged_df["date"]).astype("int64") // 86_400_000_000_000
    )
    merged_df["days_to_expiry"] = (merged_df["_exp_days"] - d_epoch).clip(lower=0)

    identity_df = merged_df[identity_cols].sort_values(
        ["date", "code"]
    ).reset_index(drop=True)
    # Dedupe by (date, code) — keep first
    identity_df = identity_df.drop_duplicates(
        subset=["date", "code"], keep="first"
    ).reset_index(drop=True)

    basic_cols_present = [c for c in basic_cols if c in np.asarray(merged_df.columns).tolist()]
    basic_df = merged_df[basic_cols_present].reindex(columns=basic_cols).sort_values(
        ["date", "code"]
    ).reset_index(drop=True)
    basic_df = basic_df.drop_duplicates(
        subset=["date", "code"], keep="first"
    ).reset_index(drop=True)

    if verbose:
        print(
            f"    [FUTURES] {n_ok} files with data, {n_empty} empty, "
            f"{n_parse_fail} rows dropped as parse failures ({n_invalid_contracts} contracts), "
            f"{len(identity_df)} identity rows, {len(basic_df)} basic_stats rows",
            flush=True,
        )

    return identity_df, basic_df