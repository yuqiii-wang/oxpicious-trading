"""Step 5 — incremental PE scope: masks, candidate selection, harmonic PE.

The write scope is defined here (it IS the PE scope):
    cand = (~exists | corp-resync codes | PE-null keys) ∩ date range
so step 6's keep-filter reuses the same masks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from _common.build_commons import parse_date, rec_cols
from _common.df_utils import safe_columns

from builds.etf.pe_aggregation import (
    fetch_stock_pe,
    compute_etf_pe_harmonic,
    extract_latest_composition,
)


@dataclass
class PeScope:
    """Shared mask machinery + PE-augmented frame."""
    merged: pd.DataFrame = None              # type: ignore[assignment]
    comp_latest: pd.DataFrame = field(default_factory=pd.DataFrame)
    in_range: np.ndarray = None              # type: ignore[assignment]
    exists: np.ndarray = None                # type: ignore[assignment]
    split_hit: pd.Series = None              # type: ignore[assignment]
    split_mask: np.ndarray = None            # type: ignore[assignment]
    pe_null_df: Optional[pd.DataFrame] = None
    pe_null_hit: np.ndarray = None           # type: ignore[assignment]
    cand: np.ndarray = None                  # type: ignore[assignment]


def _key_exists_mask(keys: pd.DataFrame, other: pd.DataFrame) -> np.ndarray:
    """Row-aligned bool: is this (date, code) present in ``other``?"""
    if other is None or other.empty:
        return np.zeros(len(keys), dtype=bool)
    left = keys.copy()
    left["_pos"] = np.arange(len(left))
    chk = left.merge(
        other.assign(_exists=True), on=["date", "code"], how="left",
    ).sort_values("_pos")
    return chk["_exists"].fillna(False).to_numpy(dtype=bool)


async def build_pe_scope(
    conn,
    merged: pd.DataFrame,
    comp_long: Optional[pd.DataFrame],
    *,
    code_filter: str | None,
    force: bool,
    start_date: str | None,
    end_date: str | None,
    existing_keys: set,
) -> PeScope:
    """Compute scope masks and merge harmonic-weighted PE into ``merged``."""
    print("\n[5/7] Computing ETF PE (harmonic-weighted constituent PE) …", flush=True)

    comp_latest = extract_latest_composition(comp_long)

    start_d = parse_date(start_date) if start_date else None
    end_d = parse_date(end_date) if end_date else None
    in_range = np.ones(len(merged), dtype=bool)
    if start_d is not None:
        in_range &= (merged["date"] >= pd.Timestamp(start_d)).to_numpy()
    if end_d is not None:
        in_range &= (merged["date"] <= pd.Timestamp(end_d)).to_numpy()

    keys = merged[["date", "code"]]
    if existing_keys:
        # sorted list, never a raw set — cudf.pandas' DataFrame constructor
        # rejects sets (fallback) and sorting keeps row order deterministic
        existing_df = pd.DataFrame(sorted(existing_keys), columns=["date", "code"])
        existing_df["date"] = pd.to_datetime(existing_df["date"])
    else:
        existing_df = pd.DataFrame(columns=["date", "code"])
    exists = _key_exists_mask(keys, existing_df)

    # CORP-ACTION RE-SYNC: per-row hit flags broadcast to every row of the
    # same code via groupby-transform (pandas native; no code-set).
    split_hit = merged["is_split_event_day"].eq(1) | (
        merged["cum_split_factor"].abs() - 1.0 > 1e-4)
    split_mask = split_hit.groupby(merged["code"]).transform("any").to_numpy()

    # DB rows whose stored PE is NULL need re-upsert to populate PE.
    pe_null_df: Optional[pd.DataFrame] = None
    if not force:
        if code_filter:
            null_pe_rows = await conn.fetch(
                "SELECT date, code FROM stats.etf_basic_stats WHERE pe IS NULL AND code = $1",
                code_filter,
            )
        else:
            null_pe_rows = await conn.fetch(
                "SELECT date, code FROM stats.etf_basic_stats WHERE pe IS NULL"
            )
        if null_pe_rows:
            pe_null_df = pd.DataFrame(rec_cols(null_pe_rows))
    if pe_null_df is not None and len(pe_null_df):
        pe_null_df["date"] = pd.to_datetime(pe_null_df["date"])
        pe_null_hit = _key_exists_mask(keys, pe_null_df[["date", "code"]])
    else:
        pe_null_hit = np.zeros(len(merged), dtype=bool)

    # PE computation scope = rows eligible for re-upsert (∩ date range).
    # Force mode: existing_keys empty → exists all-False → full scope.
    cand = (~exists | split_mask | pe_null_hit) & in_range

    merged = await compute_and_merge_pe(conn, merged, comp_latest, cand)

    n_pe_non_null = int(merged["pe"].notna().sum()) if "pe" in safe_columns(merged) else 0
    print(f"    [ETF-PE] {n_pe_non_null:,} non-null PE values in merged data", flush=True)

    return PeScope(
        merged=merged, comp_latest=comp_latest,
        in_range=in_range, exists=exists, split_hit=split_hit,
        split_mask=split_mask, pe_null_df=pe_null_df, pe_null_hit=pe_null_hit,
        cand=cand,
    )


async def compute_and_merge_pe(
    conn,
    merged: pd.DataFrame,
    comp_latest: pd.DataFrame,
    cand: np.ndarray,
) -> pd.DataFrame:
    """Fetch constituent PEs for candidate dates; merge ETF PE into merged."""
    if comp_latest.empty:
        print("    [ETF-PE] No composition data — PE will be NULL", flush=True)
        merged["pe"] = np.nan
        return merged

    # Unique constituent stock codes — extract_latest_composition already
    # validates them as canonical suffixed codes, so they match
    # stats.stock_basic_stats directly (no bare-key juggling).  Column-first:
    # boolean/nan filter then unique on the single column.
    stock_col = comp_latest["stock_code"]
    constituent_codes = np.asarray(stock_col[stock_col.notna()].unique()).tolist()
    print(f"    [ETF-PE] {len(constituent_codes):,} unique constituent stocks "
          f"across {comp_latest['etf_code'].nunique()} ETFs", flush=True)

    # Incremental: fetch stock PE ONLY for candidate dates.  Column-first
    # selection of the two needed columns via boolean mask on the mask Series.
    mask_series = pd.Series(cand, index=merged.index)
    pe_scope = merged[["code", "date"]][mask_series].reset_index(drop=True)
    # Candidate dates for the stock-PE query — one numpy transfer to
    # Python dates (Series .dt.date is a cudf fallback on datetime64).
    etf_dates = sorted(
        np.asarray(pe_scope["date"], dtype="datetime64[D]")
        .astype(object).tolist())
    if etf_dates:
        stock_pe_df = await fetch_stock_pe(
            conn, stock_codes=constituent_codes, dates=etf_dates)
    else:
        stock_pe_df = pd.DataFrame(columns=["date", "code", "pe"])
    print(f"    [ETF-PE] Fetched {len(stock_pe_df):,} stock PE rows "
          f"for {len(etf_dates):,} candidate dates "
          f"(of {merged['date'].nunique():,} total)", flush=True)

    etf_pe_df = compute_etf_pe_harmonic(pe_scope, comp_latest, stock_pe_df, verbose=True)

    if not etf_pe_df.empty:
        etf_pe_df["date"] = pd.to_datetime(etf_pe_df["date"])
        return merged.merge(etf_pe_df, on=["code", "date"], how="left")
    merged["pe"] = np.nan
    return merged
