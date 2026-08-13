"""Merge: from2020 export + 1m export + PE -> history CSV, plus incremental
xlsx -> csv append (append missing dates, never overwrite).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from downloads._common.core import (
    MIN_VALID_BYTES,
    is_valid_file,
    read_csv_preferred,
)

from ._config import logger


def _normalize_export_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the bilingual column names from the export Excel to standard names.

    The export with ``language=CH`` has bilingual headers like ``日期 Date``,
    ``开盘价 Open``, ``成交量 Volume``, etc.
    """
    rename_map: Dict[Any, Any] = {}
    for col in df.columns:
        s = str(col)
        sl = s.lower()
        if "日期" in s or sl == "date":
            rename_map[col] = "date"
        elif "代码" in s and "code" in sl:
            rename_map[col] = "indexCode"
        elif "中文全称" in s or "chinese name" in sl:
            rename_map[col] = "indexNameCnAll"
        elif "中文简称" in s:
            rename_map[col] = "indexNameCn"
        elif "英文全称" in s or "english name" in sl:
            rename_map[col] = "indexNameEnAll"
        elif "英文简称" in s:
            rename_map[col] = "indexNameEn"
        elif "开盘" in s or sl == "open":
            rename_map[col] = "open"
        elif "最高" in s or sl == "high":
            rename_map[col] = "high"
        elif "最低" in s or sl == "low":
            rename_map[col] = "low"
        elif "收盘" in s or sl == "close":
            rename_map[col] = "close"
        elif "涨跌幅" in s or "change%" in sl or "changepct" in sl or "change(" in sl:
            rename_map[col] = "changePct"
        elif "涨跌" in s or sl == "change":
            rename_map[col] = "change"
        elif "成交量" in s or "volume" in sl:
            rename_map[col] = "volume"
        elif "成交金额" in s or "turnover" in sl or "amount" in sl:
            rename_map[col] = "amount"
        elif "样本" in s or "cons" in sl:
            rename_map[col] = "consNumber"
    return df.rename(columns=rename_map)


def clean_date(val: Any) -> str:
    """Normalize a date value to YYYYMMDD string.

    Handles: "20240101", "20240101.0" (from numeric conversion),
    "2024-01-01" (hyphenated), Excel date serials (45292).
    """
    s = str(val).strip()
    if not s or s == "nan":
        return ""
    # Strip trailing ".0" from numeric conversion
    if s.endswith(".0"):
        s = s[:-2]
    # Remove date separators
    s = s.replace("-", "").replace("/", "")
    # Handle Excel date serial numbers (e.g., 45292 -> 2024-01-01)
    if s.isdigit() and len(s) <= 5:
        try:
            serial = int(s)
            if 30000 <= serial <= 80000:
                dt = datetime(1899, 12, 30) + timedelta(days=serial)
                return dt.strftime("%Y%m%d")
        except ValueError:
            pass
    return s


def build_history_csv(
    index_code: str,
    index_name: str,
    out_dir: Path,
    pe_records: List[Dict[str, Any]],
) -> Optional[Path]:
    """Merge from2020 export + 1m export + PE into a single daily history CSV.

    The 1m data overrides the from2020 data for overlapping dates (update/insert).
    PE (peg) is merged by date as a left join.
    """
    from2020_xlsx = out_dir / f"{index_code}_from2020.xlsx"
    onem_xlsx = out_dir / f"{index_code}_1m.xlsx"

    # Read with dtype=object to preserve original cell values
    df_from2020 = read_csv_preferred(from2020_xlsx, dtype=object, logger=logger, log_tag=f"[from2020 {index_code}]")
    df_1m = read_csv_preferred(onem_xlsx, dtype=object, logger=logger, log_tag=f"[1m {index_code}]")

    frames: List[pd.DataFrame] = []
    for df_raw in (df_from2020, df_1m):
        if df_raw is None or df_raw.empty:
            continue
        df = _normalize_export_columns(df_raw)
        if "date" in df.columns:
            df["date"] = df["date"].apply(clean_date)
            df = df[df["date"].str.len() == 8]
        frames.append(df)

    if not frames:
        logger.warning("  [history] %s: no export data to build history", index_code)
        return None

    # Concatenate; 1m (last) overrides from2020 for overlapping dates
    df = pd.concat(frames, ignore_index=True)
    if "date" in df.columns:
        df = df.drop_duplicates(subset=["date"], keep="last")
        df = df.sort_values("date").reset_index(drop=True)

    # Zero-pad numeric indexCode to 6 digits (Excel stores it as number, stripping leading zeros)
    # Non-numeric codes like H30007 are kept as-is
    if "indexCode" in df.columns:
        def _fix_code(v: Any) -> str:
            s = str(v).strip().split(".")[0]
            if not s:
                return ""
            if s.isdigit():
                return s.zfill(6)
            return s
        df["indexCode"] = df["indexCode"].apply(_fix_code)

    # Merge PE (peg) by date
    if pe_records:
        df_pe = pd.DataFrame(pe_records)
        if "tradeDate" in df_pe.columns and "peg" in df_pe.columns:
            df_pe = df_pe[["tradeDate", "peg"]].copy()
            df_pe["tradeDate"] = df_pe["tradeDate"].astype(str).str.strip().replace("-", "", regex=False)
            df_pe = df_pe.rename(columns={"tradeDate": "date", "peg": "pe"})
            df_pe = df_pe.drop_duplicates(subset=["date"], keep="last")
            df = df.merge(df_pe, on="date", how="left")
        else:
            logger.warning("  [history] %s: PE records missing expected fields", index_code)

    # Ensure pe column exists even if PE fetch failed
    if "pe" not in df.columns:
        df["pe"] = None

    # Add index name
    df["indexName"] = index_name

    # Select and order final columns
    preferred_cols = [
        "date", "indexCode", "indexName",
        "open", "high", "low", "close",
        "volume", "amount", "change", "changePct",
        "pe", "consNumber",
    ]
    final_cols = [c for c in preferred_cols if c in df.columns]
    df = df[final_cols]

    out_file = out_dir / f"{index_code}_history.csv"
    df.to_csv(out_file, index=False, encoding="utf-8-sig")
    logger.info(
        "  [history] saved %s (%d rows, %s~%s, pe_coverage=%d/%d)",
        out_file.name,
        len(df),
        df["date"].iloc[0] if len(df) else "?",
        df["date"].iloc[-1] if len(df) else "?",
        df["pe"].notna().sum() if "pe" in df.columns else 0,
        len(df),
    )
    return out_file


# ---------------------------------------------------------------------------
# Incremental xlsx -> csv merge (append missing dates, never overwrite)
# ---------------------------------------------------------------------------

def _find_date_column(df: pd.DataFrame) -> Optional[str]:
    """Return the date column name (bilingual 日期Date or standardized date)."""
    for col in df.columns:
        s = str(col)
        if "日期" in s or s.lower() == "date":
            return str(col)
    return None


def append_missing_dates_to_csv(
    xlsx_path: Path,
    csv_path: Path,
    index_code: str,
) -> Optional[int]:
    """Append xlsx rows whose dates are missing from the existing csv.

    Reads the xlsx directly (bilingual headers, raw values) and the existing
    csv, finds dates present in the xlsx but absent from the csv, appends only
    those rows, sorts by date, and writes the combined csv back. Existing rows
    are never modified or dropped.

    Returns the number of newly appended rows (0 if the csv already has every
    date in the xlsx), or None if the xlsx is missing/unreadable.
    """
    if not is_valid_file(xlsx_path, min_bytes=MIN_VALID_BYTES):
        return None
    try:
        df_new = pd.read_excel(xlsx_path, dtype=object)
    except Exception as e:
        logger.warning("  [1m-append] %s: xlsx read failed (%s): %s", index_code, xlsx_path.name, e)
        return None
    if df_new is None or df_new.empty:
        return None

    date_col = _find_date_column(df_new)
    if not date_col:
        logger.warning("  [1m-append] %s: no date column found in xlsx", index_code)
        return None

    new_dates = df_new[date_col].apply(clean_date)
    valid = new_dates.str.len() == 8
    df_new = df_new[valid].copy()
    new_dates = new_dates[valid]
    if df_new.empty:
        return None

    # Read existing csv (if any) and collect its dates
    existing_dates: set = set()
    df_existing: Optional[pd.DataFrame] = None
    if is_valid_file(csv_path, min_bytes=MIN_VALID_BYTES):
        try:
            df_existing = pd.read_csv(csv_path, dtype=object, encoding="utf-8-sig")
        except Exception as e:
            logger.warning("  [1m-append] %s: csv read failed (%s), rebuilding from xlsx", index_code, e)
            df_existing = None
        if df_existing is not None and not df_existing.empty:
            ex_date_col = _find_date_column(df_existing) or date_col
            if ex_date_col in df_existing.columns:
                existing_dates = set(df_existing[ex_date_col].apply(clean_date))

    # Only keep xlsx rows whose date is not already in the csv
    missing_mask = ~new_dates.isin(existing_dates)
    n_missing = int(missing_mask.sum())
    if n_missing == 0:
        return 0

    df_missing = df_new[missing_mask]
    if df_existing is not None and not df_existing.empty:
        df_combined = pd.concat([df_existing, df_missing], ignore_index=True, sort=False)
    else:
        df_combined = df_missing.copy()

    # Sort by cleaned date so the csv stays chronological
    comb_date_col = _find_date_column(df_combined) or date_col
    sort_key = df_combined[comb_date_col].apply(clean_date)
    df_combined = df_combined.assign(_sort_key=sort_key)
    df_combined = df_combined.sort_values("_sort_key").drop(columns=["_sort_key"]).reset_index(drop=True)

    df_combined.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return n_missing
