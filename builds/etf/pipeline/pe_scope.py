"""Step 5 — incremental PE scope: masks, candidate selection, harmonic PE.

The write scope is defined here (it IS the PE scope):
    cand = (~exists | corp-resync codes | PE-null keys | --date rows) ∩ date range
so step 6's keep-filter reuses the same masks. With --date, the date range
collapses to that single date and EVERY row of it is a candidate (existing
rows re-upserted via the upsert path — PeScope.forced_mask).
"""
from __future__ import annotations

import datetime

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from _common.build_commons import parse_date, rec_cols
from _common.df_utils import host_array, safe_columns

from builds.etf.pe_aggregation import (
    fetch_stock_pe,
    compute_etf_pe_harmonic,
    extract_latest_composition,
)
from builds.etf.db_query import fetch_latest_etf_composition


@dataclass
class PeScope:
    """Shared mask machinery + PE-augmented frame."""
    merged: pd.DataFrame = None              # type: ignore[assignment]
    comp_latest: pd.DataFrame = field(default_factory=pd.DataFrame)
    in_range: np.ndarray = None              # type: ignore[assignment]
    exists: np.ndarray = None                # type: ignore[assignment]
    split_hit: np.ndarray = None              # type: ignore[assignment]
    split_mask: np.ndarray = None            # type: ignore[assignment]
    pe_null_df: Optional[pd.DataFrame] = None
    pe_null_hit: np.ndarray = None            # type: ignore[assignment]
    forced_mask: np.ndarray = None            # type: ignore[assignment]
    cand: np.ndarray = None                   # type: ignore[assignment]


def _key_exists_mask(keys: pd.DataFrame, other: pd.DataFrame) -> np.ndarray:
    """Row-aligned bool: is this (date, code) present in ``other``?"""
    if other is None or other.empty:
        return np.zeros(len(keys), dtype=bool)
    left = keys.copy()
    left["_pos"] = np.arange(len(left))
    chk = left.merge(
        other.assign(_exists=True), on=["date", "code"], how="left",
    ).sort_values("_pos")
    # host unwrap — to_numpy() returns a proxy-SUBCLASS ndarray whose every
    # downstream numpy op (~exists, &, |) dispatches into cudf and logs
    # "Unsupported type ndarray"
    return host_array(chk["_exists"].fillna(False).to_numpy(dtype=bool))


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
    forced_date: datetime.date | None = None,
) -> PeScope:
    """Compute scope masks and merge harmonic-weighted PE into ``merged``.

    ``forced_date`` (--date mode) collapses the date range to that single
    date (start/end ignored) and makes every row of it a PE/write
    candidate — existing rows are refreshed via the upsert path.
    """
    print("\n[5/7] Computing ETF PE (harmonic-weighted constituent PE) …", flush=True)

    comp_latest = extract_latest_composition(comp_long)

    # Gated composition (B1): comp_long covers only NEW snapshots, so ETFs
    # whose snapshots are already stored would lose their PE. Union the
    # stored latest composition from the DB for codes not parsed this run.
    if not force:
        covered = set(np.asarray(comp_latest["etf_code"]).tolist()) \
            if len(comp_latest) else set()
        db_comp = await fetch_latest_etf_composition(
            conn, etf_codes=[code_filter] if code_filter else None)
        if len(db_comp):
            db_comp = db_comp[~db_comp["etf_code"].isin(covered)]
            if len(db_comp):
                comp_latest = pd.concat([comp_latest, db_comp], ignore_index=True)
        print(f"    [ETF-PE] comp_latest: {len(comp_latest):,} (etf, stock) pairs "
              f"across {comp_latest['etf_code'].nunique() if len(comp_latest) else 0} ETFs "
              f"(new snapshots + stored latest)", flush=True)

    start_d = parse_date(start_date) if start_date else None
    end_d = parse_date(end_date) if end_date else None
    in_range = np.ones(len(merged), dtype=bool)
    if start_d is not None:
        in_range &= (merged["date"] >= pd.Timestamp(start_d)).to_numpy()
    if end_d is not None:
        in_range &= (merged["date"] <= pd.Timestamp(end_d)).to_numpy()

    # --date mode: single-date scope — start/end are ignored and every row
    # of the forced date becomes a PE/write candidate. Host unwrap once
    # (a proxy .to_numpy() result would poison the numpy ops downstream in
    # the step-6 keep-filter).
    forced_mask = np.zeros(len(merged), dtype=bool)
    if forced_date is not None:
        forced_mask = np.asarray(host_array(merged["date"])).astype(
            "datetime64[D]") == np.datetime64(forced_date)
        in_range = forced_mask

    keys = merged[["date", "code"]]
    if existing_keys:
        # sorted list, never a raw set — cudf.pandas' DataFrame constructor
        # rejects sets (fallback) and sorting keeps row order deterministic
        existing_df = pd.DataFrame(sorted(existing_keys), columns=["date", "code"])
        existing_df["date"] = pd.to_datetime(existing_df["date"])
    else:
        existing_df = pd.DataFrame(columns=["date", "code"])
    exists = _key_exists_mask(keys, existing_df)

    # CORP-ACTION RE-SYNC (B2): cum_split_factor / cum_dividend are forward
    # products — a corp action only changes the adjustment values of its own
    # row and every row AFTER it. So a code needs re-upserting only when a
    # NEW (not-yet-in-DB) corp-action row appears, and only from that row's
    # date onward. The old mask (any |cum_split_factor|≠1 anywhere in the
    # code's history) was ever-true for 1,093 of 2,306 codes, re-upserting
    # their full history (929k rows × 5 tables) EVERY run.
    # Host-numpy event mask + int64 date ordinals; the per-code earliest-
    # new-event transform runs on GPU via a temporary int64 column
    # (Series.where(None) on datetime64 is a cudf MixedTypeError fallback
    # and groupby-by-proxy-Series is transfer-blocked). The INT64_MAX
    # sentinel reproduces the old NaT semantics: no new event → mask False.
    new_evt = (host_array(merged["is_split_event_day"]) == 1) & ~exists
    d_ord = np.asarray(host_array(merged["date"])).astype(
        "datetime64[D]").astype(np.int64)
    evt_ord = np.where(new_evt, d_ord, np.iinfo(np.int64).max)
    merged["_evt_ord"] = evt_ord
    first_ord = host_array(
        merged.groupby("code", sort=False)["_evt_ord"].transform("min"))
    merged = merged.drop(columns=["_evt_ord"])
    split_hit = new_evt
    split_mask = d_ord >= first_ord

    # DB rows whose stored PE is NULL need re-upsert to populate PE — but
    # only when PE is actually computable (B4): the harmonic PE needs a
    # composition snapshot for the code. Composition-less ETFs (every SSE
    # listing — sec_composition is SZSE-only) can NEVER get a PE, so their
    # 606,439 NULL-PE rows were re-upserted identically in every run. The
    # EXISTS subquery restricts the backfill to codes that have composition.
    pe_null_df: Optional[pd.DataFrame] = None
    if not force:
        if code_filter:
            null_pe_rows = await conn.fetch(
                """
                SELECT b.date, b.code FROM stats.etf_basic_stats b
                WHERE b.pe IS NULL AND b.code = $1
                  AND EXISTS (SELECT 1 FROM stats.sec_composition c
                              WHERE c.source_type = 'etf' AND c.code = b.code)
                """,
                code_filter,
            )
        else:
            null_pe_rows = await conn.fetch(
                """
                SELECT b.date, b.code FROM stats.etf_basic_stats b
                WHERE b.pe IS NULL
                  AND EXISTS (SELECT 1 FROM stats.sec_composition c
                              WHERE c.source_type = 'etf' AND c.code = b.code)
                """
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
    # --date mode: forced_mask puts every row of the forced date in scope
    # (existing rows re-upserted; PE recomputed for the whole date).
    cand = (~exists | split_mask | pe_null_hit | forced_mask) & in_range

    merged = await compute_and_merge_pe(conn, merged, comp_latest, cand)

    n_pe_non_null = int(merged["pe"].notna().sum()) if "pe" in safe_columns(merged) else 0
    print(f"    [ETF-PE] {n_pe_non_null:,} non-null PE values in merged data", flush=True)

    return PeScope(
        merged=merged, comp_latest=comp_latest,
        in_range=in_range, exists=exists, split_hit=split_hit,
        split_mask=split_mask, pe_null_df=pe_null_df, pe_null_hit=pe_null_hit,
        forced_mask=forced_mask, cand=cand,
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
    # stats.stock_basic_stats directly (no bare-key juggling). One host
    # transfer + np.unique: Series.unique() on cudf strings falls back
    # (returns an ExtensionArray cudf cannot convert).
    stock_col = comp_latest["stock_code"]
    stock_np = np.asarray(host_array(stock_col[stock_col.notna()]))
    constituent_codes = np.unique(stock_np).tolist()
    print(f"    [ETF-PE] {len(constituent_codes):,} unique constituent stocks "
          f"across {comp_latest['etf_code'].nunique()} ETFs", flush=True)

    # Incremental: fetch stock PE ONLY for candidate dates. Raw ndarray
    # boolean mask — a real-pandas bool Series aligned against the proxy
    # index is a cudf fallback (Unsupported type ndarray).
    # mask-filtered frame is index-blind from here (numpy date transfer +
    # row counts only) — no reindex
    pe_scope = merged[["code", "date"]][cand]
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
