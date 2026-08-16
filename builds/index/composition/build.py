"""builds.index.composition.build — Row builders for index composition.

Reads CSI + SZSE index composition CSVs and builds rows for stats.sec_composition.
"""
from __future__ import annotations

import datetime
import glob
import os

import pandas as pd

from builds._commons.paths import INDEX_COMP_DIR, SZSE_INDEX_COMP_DIR


def _read_comp_csvs(directory: str, label: str) -> pd.DataFrame:
    """Read all *_closeweight_*.csv files from a directory and return a combined DataFrame."""
    if not os.path.isdir(directory):
        print(f"    [{label}] dir not found: {directory}", flush=True)
        return pd.DataFrame()

    files = sorted(glob.glob(os.path.join(directory, "*_closeweight_*.csv")))
    if not files:
        print(f"    [{label}] no CSVs found in {directory}", flush=True)
        return pd.DataFrame()

    print(f"    [{label}] {len(files)} CSV files in {directory}", flush=True)

    dfs = []
    for path in files:
        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        except Exception:
            continue
        if df is not None and len(df) > 0:
            dfs.append(df)

    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    for c in ("snapshot_date", "index_code", "stock_code", "stock_name", "weight_pct"):
        if c not in combined.columns:
            print(f"    [{label}] WARN: missing column '{c}'", flush=True)
            return pd.DataFrame()
    combined["weight_pct"] = pd.to_numeric(combined["weight_pct"], errors="coerce").fillna(0.0)
    combined = combined.sort_values(
        ["index_code", "snapshot_date", "weight_pct"],
        ascending=[True, True, False],
    ).reset_index(drop=True)
    return combined


def _build_rows_from_df(combined: pd.DataFrame, label: str) -> list:
    """Convert a combined composition DataFrame into sec_composition row dicts."""
    if combined.empty:
        return []

    rows = []
    for (index_code, snap_date), sub in combined.groupby(["index_code", "snapshot_date"]):
        snap_date_str = str(snap_date).strip()
        try:
            snap_date_obj = datetime.datetime.strptime(snap_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        for rank_idx, (_, r) in enumerate(sub.iterrows(), start=1):
            sc = str(r.get("stock_code", "")).strip()
            sc_stripped = sc.split(".")[0].zfill(6)
            if len(sc_stripped) != 6 or not sc_stripped.isdigit():
                continue
            rows.append({
                "snapshot_date": snap_date_obj,
                "code": str(index_code).strip().zfill(6),
                "source_type": "index",
                "rank": rank_idx,
                "stock_code": sc,
                "stock_name": str(r.get("stock_name", "") or ""),
                "weight_pct": float(r["weight_pct"]),
            })

    if rows:
        n_indices = combined["index_code"].nunique()
        n_dates = combined["snapshot_date"].nunique()
        print(f"    [{label}] {len(rows):,} rows from {n_indices} indices, "
              f"{n_dates} snapshot dates", flush=True)
    return rows


def build_index_composition_rows(verbose: bool = True) -> list:
    """Read CSI index composition CSVs and build rows for stats.sec_composition."""
    combined = _read_comp_csvs(INDEX_COMP_DIR, "INDEX-COMP")
    return _build_rows_from_df(combined, "INDEX-COMP")


def build_szse_index_composition_rows(verbose: bool = True) -> list:
    """Read SZSE index composition CSVs and build rows for stats.sec_composition."""
    combined = _read_comp_csvs(SZSE_INDEX_COMP_DIR, "SZSE-INDEX-COMP")
    return _build_rows_from_df(combined, "SZSE-INDEX-COMP")
