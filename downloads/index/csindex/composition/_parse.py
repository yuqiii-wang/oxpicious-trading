"""Parse csindex.com.cn closeweight xls → normalized DataFrame."""
from __future__ import annotations

import re
from datetime import datetime
from io import BytesIO
from typing import Dict, Optional

import pandas as pd

from downloads._common import add_exchange_suffix

from ._config import COLUMN_MATCHERS, logger


def parse_closeweight_xls(raw: bytes) -> Optional[pd.DataFrame]:
    """Parse the closeweight xls bytes into a DataFrame.

    Tries pd.read_excel (xlrd) first; falls back to pd.read_html for files
    that are actually HTML tables disguised with a .xls extension.
    """
    bio = BytesIO(raw)
    try:
        df = pd.read_excel(bio, engine="xlrd")
    except Exception:
        bio.seek(0)
        try:
            tables = pd.read_html(bio)
            if tables:
                df = tables[0]
            else:
                logger.warning("[parse] read_html returned no tables")
                return None
        except Exception as e:
            logger.warning("[parse] failed to parse xls (not Excel, not HTML): %s", e)
            return None

    if df is None or len(df) == 0:
        return None
    return df


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename bilingual columns to normalized names via substring matching."""
    rename_map: Dict[str, str] = {}
    for col in df.columns:
        col_str = str(col)
        for pattern, target in COLUMN_MATCHERS:
            if pattern in col_str and target not in rename_map.values():
                rename_map[col] = target
                break
    df = df.rename(columns=rename_map)

    # Keep only the columns we care about
    keep = [t for _, t in COLUMN_MATCHERS if t in df.columns]
    return df[keep].copy()


def _extract_snapshot_date(df: pd.DataFrame) -> Optional[str]:
    """Extract the snapshot date from the snapshot_date_raw column (YYYYMMDD)."""
    if "snapshot_date_raw" not in df.columns or len(df) == 0:
        return None
    raw_val = str(df["snapshot_date_raw"].iloc[0]).strip()
    # The date is typically a string like "20260630" or a number 20260630
    m = re.search(r"(\d{8})", raw_val)
    if not m:
        return None
    try:
        d = datetime.strptime(m.group(1), "%Y%m%d")
        return d.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _normalize_stock_code(row: pd.Series) -> str:
    """Add exchange suffix to the bare stock code based on the exchange column."""
    code = str(row.get("stock_code_raw", "")).strip()
    # Strip any existing suffix
    if "." in code:
        code = code.split(".")[0]
    # Zero-pad to 6 digits
    if code.isdigit():
        code = code.zfill(6)
    exchange = str(row.get("exchange_raw", "")).strip()
    return add_exchange_suffix(code, market=exchange)


def normalize_closeweight_df(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Normalize the parsed DataFrame into the final CSV schema.

    Returns a DataFrame with columns:
      snapshot_date, index_code, index_name, stock_code, stock_name, weight_pct
    """
    df = _normalize_columns(df)
    if df.empty:
        return None

    # Extract snapshot date (same for all rows in the file)
    snapshot_date = _extract_snapshot_date(df)
    if not snapshot_date:
        logger.warning("[parse] could not extract snapshot date from file")
        return None

    # Fill index_code/index_name from the first row if present
    index_code = ""
    index_name = ""
    if "index_code" in df.columns and len(df):
        index_code = str(df["index_code"].iloc[0]).strip()
    if "index_name" in df.columns and len(df):
        index_name = str(df["index_name"].iloc[0]).strip()

    # Build stock_code with exchange suffix
    df["stock_code"] = df.apply(_normalize_stock_code, axis=1)

    # Normalize weight_pct
    df["weight_pct"] = pd.to_numeric(df.get("weight_pct"), errors="coerce").fillna(0.0)

    # Fill missing stock_name
    if "stock_name" not in df.columns:
        df["stock_name"] = ""
    df["stock_name"] = df["stock_name"].astype(str).str.strip()

    # Filter out rows with invalid stock codes (must be 6-digit + .SS/.SZ suffix)
    df = df[df["stock_code"].str.match(r"^\d{6}\.(?:SZ|SS)$", na=False)].copy()
    if df.empty:
        logger.warning("[parse] no valid stock codes after normalization")
        return None

    # Add constant columns
    df["snapshot_date"] = snapshot_date
    df["index_code"] = index_code
    df["index_name"] = index_name

    # Final column order
    result = df[["snapshot_date", "index_code", "index_name",
                 "stock_code", "stock_name", "weight_pct"]].copy()
    result = result.sort_values("weight_pct", ascending=False).reset_index(drop=True)
    return result
