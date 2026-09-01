"""builds.index.composition.build — Row builders for index composition.

Reads CSI + SZSE index composition CSVs and builds rows for stats.sec_composition.

Date-check pattern (fast-path before reading any CSV content):
  1. Glob *_closeweight_*.csv files (filenames only — no reading yet)
  2. Extract (code, snapshot_date) from each filename
  3. Query DB for existing (code, snapshot_date) pairs
  4. Filter files to only those with missing pairs
  5. Read ONLY the filtered files

--date mode (forced_date set): steps 3-4 are bypassed — only files whose
snapshot date equals forced_date are read and ALL their rows are emitted
(existing (code, snapshot_date) pairs are refreshed by the caller's upsert
write path; no deletes, no truncation). available_snapshot_dates() exposes
the filename-discovered snapshot dates so orchestrators can validate the
forced date (forced_date_scope exits(1) when it has no source data).
"""
from __future__ import annotations

import datetime
import glob
import os
import re
from typing import List, Optional, Set, Tuple

import pandas as pd

from builds._commons.paths import INDEX_COMP_DIR, SZSE_INDEX_COMP_DIR
from builds._commons.row_emission import records_from_frame
from builds._commons.safe_parse import safe_to_numeric
from _common.df_utils import safe_columns
from downloads._common import read_csv_gpu_safe


def _extract_code_date_from_filename(filename: str) -> Optional[Tuple[str, datetime.date]]:
    """Extract (index_code, snapshot_date) from '{code}_closeweight_{YYYYMMDD}.csv'.

    Returns None if the filename doesn't match the expected pattern.
    """
    basename = os.path.basename(filename)
    # Pattern: {code}_closeweight_{YYYYMMDD}.csv
    m = re.match(r"^(\d+)_closeweight_(\d{8})\.csv$", basename)
    if not m:
        return None
    code = m.group(1).zfill(6)
    ymd = m.group(2)
    try:
        snap_date = datetime.datetime.strptime(ymd, "%Y%m%d").date()
    except ValueError:
        return None
    return (code, snap_date)


def _normalize_code_filter(code_filter: str) -> str:
    """Reduce a --code value to the bare zfilled index code used in
    sec_composition (e.g. '000300.SZ' / '300' → '000300')."""
    return str(code_filter).split(".")[0].strip().zfill(6)


def _filter_files_by_code(files: List[str], code_filter: str) -> List[str]:
    """Keep only composition CSVs whose filename index code matches."""
    bare = _normalize_code_filter(code_filter)
    out = []
    for f in files:
        key = _extract_code_date_from_filename(f)
        if key and key[0] == bare:
            out.append(f)
    return out


def available_snapshot_dates(code_filter: Optional[str] = None) -> Set[datetime.date]:
    """Snapshot dates discovered from composition CSV filenames (CSI + SZSE).

    Host-only filename scan — no CSV content is read. Used by the
    orchestrators to validate a --date forced build: the forced date must
    exist among the discovered snapshot dates (forced_date_scope exits(1)
    otherwise). With code_filter, only that index's files are considered.
    """
    dates: Set[datetime.date] = set()
    bare = _normalize_code_filter(code_filter) if code_filter else None
    for directory in (INDEX_COMP_DIR, SZSE_INDEX_COMP_DIR):
        if not os.path.isdir(directory):
            continue
        for f in glob.glob(os.path.join(directory, "*_closeweight_*.csv")):
            key = _extract_code_date_from_filename(f)
            if key and (bare is None or key[0] == bare):
                dates.add(key[1])
    return dates


async def filter_comp_files_by_missing(
    conn,
    directory: str,
    label: str,
    source_type: str = "index",
    code_filter: Optional[str] = None,
    forced_date: Optional[datetime.date] = None,
) -> List[str]:
    """Filter composition CSV files to only those with missing (code, snapshot_date) in DB.

    Fast-path: reads FILENAMES only (no CSV content), extracts (code, date),
    checks against stats.sec_composition, returns only files with missing pairs.

    Args:
        conn: asyncpg connection
        directory: directory containing *_closeweight_*.csv files
        label: human-readable label for logging
        source_type: source_type column value ('index' for both CSI and SZSE)
        code_filter: optional single index code — restricts the check (and the
            files read) to that code only
        forced_date: --date mode — bypass the DB missing-pair skip and return
            ONLY the snapshot files at this date (always rebuilt; existing
            rows are refreshed by the caller's upsert write path)

    Returns:
        List of file paths to actually read.
    """
    if not os.path.isdir(directory):
        print(f"    [{label}] dir not found: {directory}", flush=True)
        return []

    files = sorted(glob.glob(os.path.join(directory, "*_closeweight_*.csv")))
    if not files:
        print(f"    [{label}] no CSVs found in {directory}", flush=True)
        return []

    # Extract (code, date) from filenames
    source_keys: Set[Tuple[str, datetime.date]] = set()
    file_to_keys: dict[str, Set[Tuple[str, datetime.date]]] = {}
    for f in files:
        key = _extract_code_date_from_filename(f)
        if key:
            source_keys.add(key)
            file_to_keys[f] = {key}

    if not source_keys:
        print(f"    [{label}] {len(files)} CSVs but no parseable filenames", flush=True)
        return []

    print(f"    [{label}] {len(files)} CSV files → {len(source_keys)} unique (code, date) pairs", flush=True)

    if code_filter:
        bare = _normalize_code_filter(code_filter)
        files = _filter_files_by_code(files, bare)
        source_keys = {k for k in source_keys if k[0] == bare}
        if not source_keys:
            print(f"    [{label}] no CSVs for code {bare} in {directory}", flush=True)
            return []

    # --date mode: bypass the DB missing-pair skip entirely — the forced
    # snapshot date is ALWAYS (re)built and its existing rows are refreshed
    # by the caller's upsert write path (no deletes, no truncation).
    # Restrict to the snapshot files at the forced date only.
    if forced_date is not None:
        forced_files = [
            f for f in files
            if any(d == forced_date for _, d in file_to_keys.get(f, ()))
        ]
        print(f"    [{label}] DATE MODE {forced_date}: {len(forced_files)} snapshot "
              f"file(s) to read (missing-pair skip bypassed)", flush=True)
        return forced_files

    # Check DB for existing pairs
    schema, tbl = "stats", "sec_composition"
    existing_query = f'''
        SELECT DISTINCT code, snapshot_date
        FROM "{schema}"."{tbl}"
        WHERE source_type = $1
    '''
    if code_filter:
        existing_query += " AND code = $2"
        existing_rows = await conn.fetch(
            existing_query, source_type, _normalize_code_filter(code_filter)
        )
    else:
        existing_rows = await conn.fetch(existing_query, source_type)
    existing_keys: Set[Tuple[str, datetime.date]] = {
        (r["code"], r["snapshot_date"]) for r in existing_rows
    }

    # Compute missing pairs
    missing_keys = source_keys - existing_keys
    if not missing_keys:
        print(f"    [{label}] All {len(source_keys)} (code, date) pairs already in DB — skipping all reads", flush=True)
        return []

    n_skipped = len(source_keys) - len(missing_keys)
    print(f"    [{label}] {len(missing_keys)} pairs missing, {n_skipped} already in DB", flush=True)

    # Filter files: only those with at least one missing key
    filtered = []
    for f in files:
        keys = file_to_keys.get(f)
        if keys is None:
            continue
        if keys & missing_keys:
            filtered.append(f)

    print(f"    [{label}] → {len(filtered)} files to read (filtered from {len(files)})", flush=True)
    return filtered


def _read_comp_csvs(directory: str, label: str, files: Optional[List[str]] = None) -> pd.DataFrame:
    """Read *_closeweight_*.csv files and return a combined DataFrame.

    If ``files`` is provided, reads only those pre-filtered files.
    Otherwise reads all files in the directory.
    """
    if files is None:
        if not os.path.isdir(directory):
            print(f"    [{label}] dir not found: {directory}", flush=True)
            return pd.DataFrame()
        files = sorted(glob.glob(os.path.join(directory, "*_closeweight_*.csv")))
        if not files:
            print(f"    [{label}] no CSVs found in {directory}", flush=True)
            return pd.DataFrame()

    if not files:
        print(f"    [{label}] no files to read (all dates already in DB)", flush=True)
        return pd.DataFrame()

    print(f"    [{label}] reading {len(files)} CSV files", flush=True)

    dfs = []
    for path in files:
        # read_csv_gpu_safe: NO encoding kwarg (cudf CPU-fallback trigger);
        # BOM stripped post-read, keep_default_na=False + na_values=[""]
        df = read_csv_gpu_safe(path, dtype=str)
        if df is not None and len(df) > 0:
            dfs.append(df)

    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    for c in ("snapshot_date", "index_code", "stock_code", "stock_name", "weight_pct"):
        if c not in safe_columns(combined):
            print(f"    [{label}] WARN: missing column '{c}'", flush=True)
            return pd.DataFrame()
    combined["weight_pct"] = safe_to_numeric(combined["weight_pct"]).fillna(0.0)
    combined = combined.sort_values(
        ["index_code", "snapshot_date", "weight_pct"],
        ascending=[True, True, False],
    ).reset_index(drop=True)
    return combined


def _build_rows_from_df(combined: pd.DataFrame, label: str,
                        default_suffix: Optional[str] = None) -> list:
    """Convert a combined composition DataFrame into sec_composition row dicts.

    ``default_suffix``: exchange suffix appended to bare 6-digit stock codes
    (e.g. ".SZ" for SZSE index closeweight CSVs, which carry bare codes).
    None keeps stock_code as-is (CSI CSVs arrive pre-suffixed and validated).
    sec_composition.stock_code must be suffixed to match stock_identity /
    stock_basic_stats for downstream joins.
    """
    if combined.empty:
        return []

    rows = []
    for (index_code, snap_date), sub in combined.groupby(["index_code", "snapshot_date"]):
        snap_date_str = str(snap_date).strip()
        try:
            snap_date_obj = datetime.datetime.strptime(snap_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        # Vectorized: filter valid stocks, assign ranks
        _sub = sub.copy()
        _sub["stock_code"] = _sub["stock_code"].astype(str).str.strip()
        _sub["sc_stripped"] = _sub["stock_code"].str.split(".").str[0].str.zfill(6)
        _sub = _sub[
            (_sub["sc_stripped"].str.len() == 6) &
            _sub["sc_stripped"].str.isdigit()
        ].copy()
        if default_suffix:
            _sub["stock_code"] = _sub["sc_stripped"] + default_suffix
        if len(_sub) > 0:
            _sub["rank"] = range(1, len(_sub) + 1)
            _sub["snapshot_date"] = snap_date_obj
            _sub["code"] = str(index_code).strip().zfill(6)
            _sub["source_type"] = "index"
            _sub["stock_name"] = _sub["stock_name"].fillna("").astype(str)
            rows.extend(records_from_frame(
                _sub, ["snapshot_date", "code", "source_type", "rank",
                       "stock_code", "stock_name", "weight_pct"]
            ))

    if rows:
        n_indices = combined["index_code"].nunique()
        n_dates = combined["snapshot_date"].nunique()
        print(f"    [{label}] {len(rows):,} rows from {n_indices} indices, "
              f"{n_dates} snapshot dates", flush=True)
    return rows


async def build_index_composition_rows(conn=None, force: bool = False,
                                       code_filter: Optional[str] = None,
                                       forced_date: Optional[datetime.date] = None) -> list:
    """Read CSI index composition CSVs and build rows for stats.sec_composition.

    With a DB connection, first filters files by missing (code, snapshot_date)
    before reading any CSV content. With code_filter, only that index's files
    are considered (both in the DB check and the CSV read). With forced_date
    (--date mode), only that snapshot date's files are read and the
    missing-pair skip is bypassed (rows already in the DB are refreshed by
    the caller's upsert).
    """
    if conn is not None and not force:
        filtered_files = await filter_comp_files_by_missing(
            conn, INDEX_COMP_DIR, "INDEX-COMP", code_filter=code_filter,
            forced_date=forced_date,
        )
        combined = _read_comp_csvs(INDEX_COMP_DIR, "INDEX-COMP", files=filtered_files)
    elif code_filter:
        all_files = sorted(glob.glob(os.path.join(INDEX_COMP_DIR, "*_closeweight_*.csv")))
        combined = _read_comp_csvs(
            INDEX_COMP_DIR, "INDEX-COMP",
            files=_filter_files_by_code(all_files, code_filter),
        )
    else:
        combined = _read_comp_csvs(INDEX_COMP_DIR, "INDEX-COMP")
    return _build_rows_from_df(combined, "INDEX-COMP")


async def build_szse_index_composition_rows(conn=None, force: bool = False,
                                            code_filter: Optional[str] = None,
                                            forced_date: Optional[datetime.date] = None) -> list:
    """Read SZSE index composition CSVs and build rows for stats.sec_composition.

    With a DB connection, first filters files by missing (code, snapshot_date)
    before reading any CSV content. With code_filter, only that index's files
    are considered (both in the DB check and the CSV read). With forced_date
    (--date mode), only that snapshot date's files are read and the
    missing-pair skip is bypassed (rows already in the DB are refreshed by
    the caller's upsert).
    """
    if conn is not None and not force:
        filtered_files = await filter_comp_files_by_missing(
            conn, SZSE_INDEX_COMP_DIR, "SZSE-INDEX-COMP", code_filter=code_filter,
            forced_date=forced_date,
        )
        combined = _read_comp_csvs(SZSE_INDEX_COMP_DIR, "SZSE-INDEX-COMP", files=filtered_files)
    elif code_filter:
        all_files = sorted(glob.glob(os.path.join(SZSE_INDEX_COMP_DIR, "*_closeweight_*.csv")))
        combined = _read_comp_csvs(
            SZSE_INDEX_COMP_DIR, "SZSE-INDEX-COMP",
            files=_filter_files_by_code(all_files, code_filter),
        )
    else:
        combined = _read_comp_csvs(SZSE_INDEX_COMP_DIR, "SZSE-INDEX-COMP")
    return _build_rows_from_df(combined, "SZSE-INDEX-COMP", default_suffix=".SZ")
