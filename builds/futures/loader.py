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
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from builds.futures.config import (
    COL_MAP,
    NUMERIC_COLS,
    PRODUCT_NAMES,
    PRODUCT_TYPES,
    PRODUCT_UNDERLYING,
    _NULL_TOKENS,
    compute_expiry_date,
    normalize_contract_year_month,
    parse_contract_code,
)
from builds.futures.paths import CFFEX_ARCHIVE_DIR, FUTURES_CSV_PATTERN

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

    Args:
        files: list of file paths
        target_dates: set of pd.Timestamp dates to keep

    Returns:
        Filtered list of file paths.
    """
    target_ymd = {d.strftime("%Y%m%d") for d in target_dates}
    out: List[str] = []
    for path in files:
        ymd = ymd_from_futures_filename(path)
        if ymd and ymd in target_ymd:
            out.append(path)
    return out


def _read_one_csv(filepath: str) -> Optional[pd.DataFrame]:
    """Read a single futures CSV file and return a clean DataFrame.

    Handles:
      - UTF-8 BOM (files saved with BOM from download step)
      - Trailing whitespace in contract codes (older files)
      - "--" as null value for numeric columns
      - Numeric coercion for all numeric columns

    Returns DataFrame or None if file is empty/unreadable.
    """
    try:
        df = pd.read_csv(filepath, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    except Exception:
        try:
            df = pd.read_csv(filepath, dtype=str, encoding="utf-8", keep_default_na=False)
        except Exception:
            return None

    if df is None or len(df) == 0:
        return None

    # Strip whitespace from all string columns
    df = df.apply(lambda c: c.str.strip() if c.dtype == "object" else c)

    # Check for required column
    if "合约代码" not in df.columns:
        return None

    # Filter out rows with empty/whitespace-only contract codes
    df = df[df["合约代码"].notna()].copy()
    df = df[df["合约代码"].str.len() > 0].copy()
    if df.empty:
        return None

    # Rename columns
    df = df.rename(columns=COL_MAP)

    # Convert numeric columns
    for col in NUMERIC_COLS:
        if col in df.columns:
            # First replace null tokens with NaN
            df[col] = df[col].apply(
                lambda v: np.nan if str(v).strip() in _NULL_TOKENS else v
            )
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows with invalid codes (can't parse product)
    if "code" in df.columns:
        valid_mask = df["code"].apply(_is_valid_code)
        df = df[valid_mask].copy()

    return df if not df.empty else None


def _is_valid_code(code: str) -> bool:
    """Check if a contract code has a valid product prefix."""
    try:
        parse_contract_code(code)
        return True
    except ValueError:
        return False


def build_futures_df(
    files: List[str],
    verbose: bool = True,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Build identity and basic_stats DataFrames from a list of futures CSV files.

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

    all_identity_rows: List[dict] = []
    all_basic_rows: List[dict] = []
    n_empty = 0
    n_ok = 0
    n_parse_fail = 0

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

        date_str = date_ts.strftime("%Y-%m-%d")

        for _, row in df.iterrows():
            code = str(row.get("code", "")).strip()
            try:
                product_code, contract_month = parse_contract_code(code)
            except ValueError:
                n_parse_fail += 1
                continue

            contract_year_month = normalize_contract_year_month(contract_month)
            contract_type = PRODUCT_TYPES.get(product_code, "unknown")
            name = PRODUCT_NAMES.get(product_code, product_code)
            underlying_code, underlying_name = PRODUCT_UNDERLYING.get(
                product_code, ("", "")
            )

            expiry_date = compute_expiry_date(contract_month, contract_type)
            days_to_expiry = max(0, (expiry_date - date_ts.date()).days)

            # Identity row
            all_identity_rows.append({
                "date": date_ts.date(),
                "code": code,
                "product_code": product_code,
                "contract_month": contract_month,
                "contract_year_month": contract_year_month,
                "contract_type": contract_type,
                "name": name,
                "underlying_code": underlying_code,
                "underlying_name": underlying_name,
                "days_to_expiry": days_to_expiry,
            })

            # Basic stats row
            basic_row = {
                "date": date_ts.date(),
                "code": code,
            }
            for col in (
                "open", "high", "low", "close",
                "settlement_price", "prev_settlement",
                "change", "change_pct",
                "trading_shares", "trading_amount",
                "open_interest", "open_interest_change",
                "delta",
            ):
                basic_row[col] = row.get(col, np.nan)
            all_basic_rows.append(basic_row)

        n_ok += 1

    if verbose:
        print(
            f"    [FUTURES] {n_ok} files with data, {n_empty} empty, "
            f"{n_parse_fail} parse failures, "
            f"{len(all_identity_rows)} identity rows, {len(all_basic_rows)} basic_stats rows",
            flush=True,
        )

    if not all_identity_rows:
        empty_id = pd.DataFrame(columns=[
            "date", "code", "product_code", "contract_month",
            "contract_year_month", "contract_type", "name",
            "underlying_code", "underlying_name", "days_to_expiry",
        ])
        empty_bs = pd.DataFrame(columns=[
            "date", "code", "open", "high", "low", "close",
            "settlement_price", "prev_settlement", "change", "change_pct",
            "trading_shares", "trading_amount",
            "open_interest", "open_interest_change", "delta",
        ])
        return empty_id, empty_bs

    identity_df = pd.DataFrame(all_identity_rows)
    identity_df = identity_df.sort_values(
        ["date", "code"]
    ).reset_index(drop=True)
    # Dedupe by (date, code) — keep first
    identity_df = identity_df.drop_duplicates(
        subset=["date", "code"], keep="first"
    ).reset_index(drop=True)

    basic_df = pd.DataFrame(all_basic_rows)
    basic_df = basic_df.sort_values(
        ["date", "code"]
    ).reset_index(drop=True)
    # Dedupe by (date, code) — keep first
    basic_df = basic_df.drop_duplicates(
        subset=["date", "code"], keep="first"
    ).reset_index(drop=True)

    return identity_df, basic_df