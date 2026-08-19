"""build_szse_sse_etf_and_margin.py — Build combined SZSE + SSE ETF OHLCV +
margin + composition + PE data and insert directly to the database
(missing-data-only).

NOTE: This script loads ONLY ETF data. Index composition (CSI + SZSE
closeweight CSVs) is now loaded by `python -m builds.index.composition`
which writes to the same stats.sec_composition table with
source_type='index'. Run builds.index.composition BEFORE builds.index.baseline
so that index shared weights are available for close-price estimation.

Reads the per-day SZSE/SSE CSV archives produced by download scripts:
  - SZSE: szse_archive/szse_etf_YYYYMMDD.csv        (2022-01 → 2025-06-30 legacy)
  - SZSE: szse_trend/szse_trend_etf_YYYYMMDD.csv    (2025-07 → today snapshot)
  - SSE: sse_trend/sse_trend_stock_YYYYMMDD.csv     (today snapshot, stocks only — NO ETFs)
  - SZSE: szse_margin/szse_margin_detail_YYYYMMDD.csv  (per-security margin detail)
  - SZSE: szse_etf_composition/szse_etf_comp_YYYYMMDD_<code>.csv (per-file finished CSV)

ETF PE: computed via HARMONIC weighting of constituent stock PE from
stats.stock_basic_stats by the LATEST stats.sec_composition snapshot
(source_type='etf', temporal extrapolation):
    PE_etf = SUM(w_i) / SUM(w_i / PE_i)
Loss-making constituents (NULL PE) are excluded from both numerator and
denominator. The merge + groupby-agg steps use cuDF when worthwhile
(should_use_gpu router). Run builds.stock BEFORE builds.etf so stock PE
is available.

Missing-data detection flow (DB-first):
  OHLCV + margin (cross-date dependency — splits + MAs need FULL per-code history):
    1. Glob all source CSV files (filenames only — no reading yet)
    2. Extract available dates from filenames
    3. Query stats.etf_identity by index for existing (date, code) pairs
    4. missing_dates = available_dates - existing_dates
    5. If no missing dates: query DB for historical OHLCV+margin only (for
       composition merge_asof + sec_classification stats), skip CSV reading entirely
    6. If missing dates exist: read ONLY the source CSVs for those missing
       dates, then query DB for existing OHLCV+margin (historical context
       for split adjustment + MA computation), and concatenate the two
    7. Merge OHLCV + margin, apply split adjustment, compute MAs (over the
       combined full history)
    8. Compute ETF PE (harmonic-weighted constituent PE)
    9. Filter merged to (date, code) NOT in existing_keys [and within
       --start/--end range]
    10. Bulk upsert only the missing rows into etf_identity + 5 sub-tables

  ETF composition (sec_composition source_type='etf' — no cross-date dependency):
    1. Read all ETF composition CSVs, build holdings rows
    2. Query stats.sec_composition for existing (code, snapshot_date) pairs
       (covers both source_type='etf' and 'index' — index rows are owned by
       builds.index.composition and are never touched here)
    3. Filter to missing (code, snapshot_date) pairs
    4. Bulk upsert only the missing rows

  ETF meta (sec_classification type='etf' — per-code metadata, not per-date):
    Computed from full merged data (n_days, avg_shares, etc.) and
    upserted unconditionally (ON CONFLICT DO UPDATE — idempotent). Only
    quality-metric columns are updated; classification + index_code columns
    (populated by build_etf_index_map.py) are preserved on conflict.

With --force: truncate stats.etf_identity and DELETE FROM stats.sec_composition
WHERE source_type='etf' (index composition rows are preserved), then read ALL
source CSVs (no DB historical query needed since DB is empty).

Usage:
  python -m builds.etf
  python -m builds.etf --start-date 2024-01-01 --end-date 2025-06-30
  python -m builds.etf --force
"""
import os, sys, time, argparse
import datetime

import warnings
warnings.filterwarnings("ignore")

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import numpy as np
import pandas as pd

from downloads._common.core import strip_exchange_suffix
from _common.build_commons import (
    setup_utf8_stdout, add_common_build_args, get_db_or_exit,
    parse_date, ymd_from_filename, ymd_to_date,
    glob_source_files, print_build_header, print_wall_time,
    TODAY_STR,
    get_existing_keys_async, copy_or_upsert_split_async, bulk_upsert_async, truncate_table_async,
    compute_eps,
)
from _common.df_utils import compute_moving_averages, compute_emas

setup_utf8_stdout()

import asyncio

from builds.etf.paths import (
    SZSE_ARCHIVE_DIR, SZSE_TREND_DIR, SSE_TREND_DIR,
    SZSE_MARGIN_DIR, SSE_MARGIN_DIR, COMP_DIR,
)
from builds.etf.codes import get_exchange_for_etf
from builds.etf.split_adjustment import apply_split_adjustment
from builds.etf.composition import build_composition
from builds.etf.ohlcv import build_ohlcv_df
from builds.etf.margin import build_margin_df
from builds.etf.theme import classify_etf_theme
from builds.etf.db_query import query_existing_ohlcv_margin_from_db
from builds.etf.pe_aggregation import (
    fetch_stock_pe,
    compute_etf_pe_harmonic,
    extract_latest_composition,
)


# ============================================================================
# Main pipeline
# ============================================================================
async def main():
    ap = argparse.ArgumentParser(
        description="Build SZSE + SSE ETF + margin + composition + PE and insert to database (missing-data-only)."
    )
    add_common_build_args(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "BUILD SZSE + SSE ETF + MARGIN + COMPOSITION + PE  ·  missing-data-only → DATABASE",
        **{
            "SZSE Archive dir": SZSE_ARCHIVE_DIR,
            "SZSE Trend dir":   SZSE_TREND_DIR,
            "Margin dir":       SZSE_MARGIN_DIR,
            "Composition dir":  COMP_DIR,
            "Date range":       f"{args.start_date or '(all)'} → {args.end_date or '(all)'}",
            "Today":            TODAY_STR,
        }
    )

    # ------------------------------------------------------------------
    # (1) Discover source files (fast — filenames only, no reading)
    # ------------------------------------------------------------------
    print("\n[1/7] Discovering source CSV files …", flush=True)
    szse_archive_files = glob_source_files(SZSE_ARCHIVE_DIR, "szse_etf_*.csv")
    szse_trend_files   = glob_source_files(SZSE_TREND_DIR, "szse_trend_etf_*.csv")
    sse_trend_files    = glob_source_files(SSE_TREND_DIR, "sse_trend_stock_*.csv")
    szse_margin_files  = glob_source_files(SZSE_MARGIN_DIR, "szse_margin_detail_*.csv")
    sse_margin_files   = glob_source_files(SSE_MARGIN_DIR, "sse_margin_detail_*.csv")

    # Extract available OHLCV dates from filenames
    available_ohlcv_dates = set()
    for f in szse_archive_files:
        ymd = ymd_from_filename(f, "szse_etf_")
        if ymd:
            d = ymd_to_date(ymd)
            if d:
                available_ohlcv_dates.add(d)
    for f in szse_trend_files:
        ymd = ymd_from_filename(f, "szse_trend_etf_")
        if ymd:
            d = ymd_to_date(ymd)
            if d:
                available_ohlcv_dates.add(d)

    print(f"    → OHLCV: {len(szse_archive_files)} szse_archive + "
          f"{len(szse_trend_files)} szse_trend + {len(sse_trend_files)} sse_trend files", flush=True)
    print(f"    → Margin: {len(szse_margin_files)} szse + {len(sse_margin_files)} sse files", flush=True)
    print(f"    → {len(available_ohlcv_dates)} unique OHLCV dates available in source files", flush=True)

    # ------------------------------------------------------------------
    # (2) Connect to DB and find missing dates
    # ------------------------------------------------------------------
    print("\n[2/7] Connecting to database and detecting missing dates …", flush=True)
    conn = await get_db_or_exit()

    try:
        if args.force:
            print("    [DB] Force mode: truncating ETF tables", flush=True)
            await truncate_table_async(conn, "stats.etf_identity")
            # NOTE: stats.sec_composition is shared between ETF composition
            # (source_type='etf', loaded here) and index composition
            # (source_type='index', loaded by builds.index.composition). Only
            # delete ETF rows to preserve index composition data.
            await conn.execute(
                "DELETE FROM stats.sec_composition WHERE source_type = 'etf'"
            )
            existing_keys = set()
            existing_dates = set()
        else:
            existing_keys = await get_existing_keys_async(
                conn, "stats.etf_identity", ["date", "code"]
            )
            existing_dates = {d for (d, _c) in existing_keys}

        missing_ohlcv_dates = available_ohlcv_dates - existing_dates
        print(f"    [DB] {len(existing_keys):,} existing (date, code) pairs in stats.etf_identity", flush=True)
        print(f"    [DB] {len(missing_ohlcv_dates)} dates missing "
              f"(out of {len(available_ohlcv_dates)} available)", flush=True)

        # ------------------------------------------------------------------
        # Recent-date re-scan — catch newly-listed ETFs whose (date, code)
        # pairs are absent from already-loaded dates.
        # ------------------------------------------------------------------
        RECENT_REFRESH_DAYS = 30
        max_available = max(available_ohlcv_dates) if available_ohlcv_dates else None
        recent_refresh_dates: set = set()
        if max_available is not None:
            cutoff = max_available - datetime.timedelta(days=RECENT_REFRESH_DAYS)
            recent_refresh_dates = {
                d for d in (available_ohlcv_dates & existing_dates) if d >= cutoff
            }
        dates_to_read = missing_ohlcv_dates | recent_refresh_dates
        if recent_refresh_dates:
            print(f"    [DB] {len(recent_refresh_dates)} recent dates (last {RECENT_REFRESH_DAYS}d) "
                  f"re-scanned for newly-listed ETFs", flush=True)

        # ------------------------------------------------------------------
        # (3) Read ONLY missing-date source CSVs + query DB for historical context
        # ------------------------------------------------------------------
        if args.force:
            print("\n[3/7] Reading ALL source CSVs (force mode) …", flush=True)
            ohlcv_df = build_ohlcv_df(verbose=True)
            margin_df = build_margin_df(verbose=True)
        elif not dates_to_read:
            print("\n[3/7] OHLCV up to date — querying DB for historical context only …", flush=True)
            ohlcv_df, margin_df = await query_existing_ohlcv_margin_from_db(conn, verbose=True)
        else:
            print(f"\n[3/7] Reading source CSVs for {len(missing_ohlcv_dates)} missing + "
                  f"{len(recent_refresh_dates)} recent dates "
                  f"+ querying DB for historical context …", flush=True)
            read_ymd = {d.strftime("%Y%m%d") for d in dates_to_read}

            missing_szse_archive = [f for f in szse_archive_files
                                    if ymd_from_filename(f, "szse_etf_") in read_ymd]
            missing_szse_trend   = [f for f in szse_trend_files
                                    if ymd_from_filename(f, "szse_trend_etf_") in read_ymd]
            missing_sse_trend    = [f for f in sse_trend_files
                                    if ymd_from_filename(f, "sse_trend_stock_") in read_ymd]
            missing_szse_margin  = [f for f in szse_margin_files
                                    if ymd_from_filename(f, "szse_margin_detail_") in read_ymd]
            missing_sse_margin   = [f for f in sse_margin_files
                                    if ymd_from_filename(f, "sse_margin_detail_") in read_ymd]

            print(f"    → OHLCV files to read: {len(missing_szse_archive)} szse_archive + "
                  f"{len(missing_szse_trend)} szse_trend + {len(missing_sse_trend)} sse_trend", flush=True)
            print(f"    → Margin files to read: {len(missing_szse_margin)} szse + "
                  f"{len(missing_sse_margin)} sse", flush=True)

            ohlcv_file_sets = {
                "szse_archive": missing_szse_archive,
                "szse_trend":   missing_szse_trend,
                "sse_trend":    missing_sse_trend,
            }
            new_ohlcv_df = build_ohlcv_df(verbose=True, ohlcv_files=ohlcv_file_sets)

            margin_file_sets = {
                "szse": missing_szse_margin,
                "sse":  missing_sse_margin,
            }
            new_margin_df = build_margin_df(verbose=True, margin_files=margin_file_sets)

            # Query historical OHLCV + margin from DB (for split/MA correctness)
            hist_ohlcv_df, hist_margin_df = await query_existing_ohlcv_margin_from_db(conn, verbose=True)

            # Combine historical (DB) + new (CSV) — keep last for overlapping keys
            if len(hist_ohlcv_df) and len(new_ohlcv_df):
                ohlcv_df = pd.concat([hist_ohlcv_df, new_ohlcv_df], ignore_index=True)
                ohlcv_df = ohlcv_df.drop_duplicates(subset=["date", "code"], keep="last")
            elif len(new_ohlcv_df):
                ohlcv_df = new_ohlcv_df
            else:
                ohlcv_df = hist_ohlcv_df
            ohlcv_df = ohlcv_df.sort_values(["code", "date"]).reset_index(drop=True)

            if len(hist_margin_df) and len(new_margin_df):
                margin_df = pd.concat([hist_margin_df, new_margin_df], ignore_index=True)
                margin_df = margin_df.drop_duplicates(subset=["date", "code"], keep="last")
            elif len(new_margin_df):
                margin_df = new_margin_df
            else:
                margin_df = hist_margin_df
            if len(margin_df):
                margin_df = margin_df.sort_values(["code", "date"]).reset_index(drop=True)

        if len(ohlcv_df) == 0:
            print("    [FATAL] No OHLCV rows to process — check source files and DB", flush=True)
            sys.exit(1)
        if len(margin_df) == 0:
            print("    [WARN] No margin rows — proceeding with OHLCV only", flush=True)
            margin_df = pd.DataFrame(columns=["date", "code", "rz_buy", "rz_balance",
                                              "rq_sell_qty", "rq_balance_qty",
                                              "rq_balance_amt", "total_balance"])

        # ------------------------------------------------------------------
        # (4) Merge OHLCV + margin, apply split adjustment + MAs (full history)
        # ------------------------------------------------------------------
        print("\n[4/7] Merging OHLCV + margin, applying corp-action adjustment + MAs …", flush=True)
        if len(margin_df):
            merged = ohlcv_df.merge(margin_df, on=["date", "code"], how="left", validate="m:1")
        else:
            merged = ohlcv_df.copy()
            for c in ["rz_buy", "rz_balance", "rq_sell_qty", "rq_balance_qty",
                      "rq_balance_amt", "total_balance"]:
                merged[c] = 0.0
        for c in ["rz_buy", "rz_balance", "rq_sell_qty", "rq_balance_qty",
                  "rq_balance_amt", "total_balance"]:
            if c in merged.columns:
                merged[c] = pd.to_numeric(merged[c], errors="coerce").fillna(0.0)

        # Compute rq_balance_amt for SSE ETFs (quantity × mid price)
        has_rq_qty = "rq_balance_qty" in merged.columns and (merged["rq_balance_qty"] > 0).any()
        has_rq_amt = "rq_balance_amt" in merged.columns
        if has_rq_qty and has_rq_amt:
            sse_mask = merged["code"].str.endswith(".SS")
            missing_rq_amt = sse_mask & (merged["rq_balance_amt"] == 0) & (merged["rq_balance_qty"] > 0)
            if missing_rq_amt.any():
                mid_price = (
                    merged.loc[missing_rq_amt, "open"] + merged.loc[missing_rq_amt, "close"]
                ) / 2.0
                merged.loc[missing_rq_amt, "rq_balance_amt"] = (
                    merged.loc[missing_rq_amt, "rq_balance_qty"] * mid_price
                )
                print(f"    → Filled rq_balance_amt for {missing_rq_amt.sum():,} SSE ETF rows", flush=True)

        # Apply corp-action adjustment (needs full per-code history)
        merged = apply_split_adjustment(merged, verbose=True)

        # Compute MAs (needs full per-code history)
        merged = merged.sort_values(["code", "date"]).reset_index(drop=True)
        compute_moving_averages(
            merged,
            group_key="code",
            value_col="adj_close",
            windows=[5, 20, 60, 120, 255],
        )
        compute_emas(
            merged,
            group_key="code",
            value_col="adj_close",
            spans=[6, 10, 20, 60, 120, 255],
        )
        print(f"    → MA columns added: ma5, ma5_ratio, ma20, ma60, ma120, ma255; "
              f"EMA columns added: ema6, ema10, ema20, ema60, ema120, ema255", flush=True)

        # ------------------------------------------------------------------
        # (4b) Build composition (for sec_composition insertion + PE)
        # ------------------------------------------------------------------
        print("\n    Building composition …", flush=True)
        comp_long, comp_universe = build_composition(verbose=True)

        # ------------------------------------------------------------------
        # (5) Compute ETF PE (harmonic-weighted constituent PE)
        # ------------------------------------------------------------------
        print("\n[5/7] Computing ETF PE (harmonic-weighted constituent PE) …", flush=True)

        # Extract latest composition snapshot from in-memory comp_long.
        # Falls back to empty if no composition data.
        comp_latest = extract_latest_composition(comp_long)

        if not comp_latest.empty:
            # Get unique constituent stock codes (bare, no suffix)
            constituent_codes = sorted(
                comp_latest["stock_code"].apply(
                    lambda s: str(s).split(".")[0].zfill(6)
                ).unique().tolist()
            )
            print(f"    [ETF-PE] {len(constituent_codes):,} unique constituent stocks "
                  f"across {comp_latest['etf_code'].nunique()} ETFs", flush=True)

            # Fetch stock PE from DB for the dates we need.
            # We compute PE for ALL dates in merged (full history) so it
            # flows through the same _should_upsert filter as OHLCV.
            etf_dates = sorted(merged["date"].dt.date.unique().tolist())
            stock_pe_df = await fetch_stock_pe(
                conn, stock_codes=constituent_codes, dates=etf_dates
            )
            print(f"    [ETF-PE] Fetched {len(stock_pe_df):,} stock PE rows "
                  f"for {len(etf_dates):,} dates", flush=True)

            # Compute harmonic-weighted PE per (etf, date)
            etf_pe_df = compute_etf_pe_harmonic(
                merged[["code", "date"]], comp_latest, stock_pe_df, verbose=True
            )

            # Merge PE into merged
            if not etf_pe_df.empty:
                etf_pe_df["date"] = pd.to_datetime(etf_pe_df["date"])
                merged = merged.merge(etf_pe_df, on=["code", "date"], how="left")
            else:
                merged["pe"] = np.nan
        else:
            print("    [ETF-PE] No composition data — PE will be NULL", flush=True)
            merged["pe"] = np.nan

        n_pe_non_null = merged["pe"].notna().sum() if "pe" in merged.columns else 0
        print(f"    [ETF-PE] {n_pe_non_null:,} non-null PE values in merged data", flush=True)

        # ------------------------------------------------------------------
        # (5b) Build universe (for sec_classification stats — from FULL merged data)
        # ------------------------------------------------------------------
        uni_rows = []
        for code, sub in merged.groupby("code"):
            name = str(sub["name"].dropna().iloc[0]) if sub["name"].notna().any() else ""
            tid, tlabel, tslug = classify_etf_theme(name)
            code_base = strip_exchange_suffix(code)
            has_comp = comp_universe is not None and code_base in comp_universe["etf_code"].values
            n_comp_dates = 0
            n_holdings_latest = 0
            if has_comp:
                cu = comp_universe[comp_universe["etf_code"] == code_base].iloc[0]
                n_comp_dates = int(cu.get("n_dates", 0))
                n_holdings_latest = int(cu.get("n_holdings_latest", 0))
            exchange = "SZ" if code.endswith(".SZ") else "SS"
            uni_rows.append({
                "code":             code,
                "exchange":         exchange,
                "name":             name,
                "n_ohlcv_days":     int(len(sub)),
                "n_margin_days":    int((sub["rz_balance"] > 0).sum()) if "rz_balance" in sub.columns else 0,
                "n_comp_dates":     n_comp_dates,
                "n_holdings_latest": n_holdings_latest,
                "first_date":       sub["date"].min().strftime("%Y-%m-%d") if len(sub) else "",
                "last_date":        sub["date"].max().strftime("%Y-%m-%d") if len(sub) else "",
                "theme_id":         tid,
                "theme_label":      tlabel,
                "theme_slug":       tslug,
            })
        uni_df = pd.DataFrame(uni_rows).sort_values(["theme_id", "n_ohlcv_days"],
                                                     ascending=[True, False])

        # ------------------------------------------------------------------
        # (6) Filter to missing (date, code) pairs and insert OHLCV/margin tables
        #
        # CORP-ACTION RE-SYNC: codes with any detected split/dividend event
        # (is_split_event_day=1 OR cum_split_factor deviates from 1.0) must
        # have ALL their rows re-upserted — not just missing ones. Otherwise
        # the cumulative split factor fails to propagate to rows inserted
        # before the event day was backfilled.
        # ------------------------------------------------------------------
        print("\n[6/7] Filtering to missing (date, code) pairs and inserting …", flush=True)

        # Identify codes whose adjustment factors must be re-synced
        split_affected_codes: set = set(
            merged.loc[merged["is_split_event_day"] == 1, "code"].unique()
        )
        split_affected_codes |= set(
            merged.loc[merged["cum_split_factor"].abs() - 1.0 > 1e-4, "code"].unique()
        )
        if split_affected_codes:
            print(f"    [CORP-RESYNC] {len(split_affected_codes)} codes with corp-action "
                  f"events — re-upserting ALL their rows (not just missing)", flush=True)

        # Query DB for (date, code) pairs where PE is NULL — these need
        # re-upsert to populate the newly-added PE column.
        pe_null_keys: set = set()
        if not args.force and "pe" in merged.columns:
            null_pe_rows = await conn.fetch(
                "SELECT date, code FROM stats.etf_basic_stats WHERE pe IS NULL"
            )
            pe_null_keys = {(r["date"], r["code"]) for r in null_pe_rows}
            if pe_null_keys:
                print(f"    [PE-BACKFILL] {len(pe_null_keys):,} existing rows with NULL PE "
                      f"— re-upserting to populate PE", flush=True)

        # Filter merged to missing (date, code) pairs [and within --start/--end range]
        merged_db = merged.copy()
        merged_db["date"] = merged_db["date"].dt.date

        start_d = parse_date(args.start_date) if args.start_date else None
        end_d = parse_date(args.end_date) if args.end_date else None

        def _should_upsert(row):
            d = row["date"]
            if start_d and d < start_d:
                return False
            if end_d and d > end_d:
                return False
            if (d, row["code"]) not in existing_keys:
                return True
            if row["code"] in split_affected_codes:
                return True
            # Re-upsert rows with newly computed PE where DB has NULL PE
            if pd.notna(row.get("pe")) and (d, row["code"]) in pe_null_keys:
                return True
            return False

        missing_mask = merged_db.apply(_should_upsert, axis=1)
        merged_missing = merged_db[missing_mask].reset_index(drop=True)
        print(f"    [DB] {len(merged_missing):,} rows to upsert "
              f"(out of {len(merged_db):,} total, missing + corp-action resync)", flush=True)

        if len(merged_missing) == 0 and not args.force:
            print("    [INFO] etf_identity is up to date — no new OHLCV/margin rows to insert", flush=True)
        else:
            # Dedupe within the batch
            merged_missing = merged_missing.drop_duplicates(subset=["date", "code"], keep="last")

            # Build rows for each split table — vectorized
            _src = merged_missing.copy()
            _src["code"] = _src["code"].astype(str)
            # --- Helper: vectorized NaN→None ---
            def _to_db_series(s: pd.Series) -> pd.Series:
                return s.where(s.notna(), None)
            # --- Helper: vectorized EPS ---
            def _compute_eps_vec(close: pd.Series, pe: pd.Series) -> pd.Series:
                mask = close.notna() & pe.notna() & (pe > 0)
                vals = (close[mask].astype(float) / pe[mask].astype(float)).round(6)
                result = pd.Series([None] * len(close), index=close.index, dtype=object)
                result[mask] = vals
                return result
            # --- code_suffix ---
            _has_dot = _src["code"].str.contains(r"\.", na=False)
            _suffixes = _src["code"].str.split(".").str[-1]
            _src["code_suffix"] = np.where(
                _has_dot & _suffixes.isin(["SZ", "SS", "SH"]),
                _suffixes, "",
            )
            # --- identity_rows ---
            _src["name"] = _src["name"].where(_src["name"].notna(), "").astype(str)
            identity_rows = _src[["date", "code", "code_suffix", "name"]].to_dict(orient="records")
            # --- basic_rows ---
            _basic_cols = ["prev_close", "open", "high", "low", "close", "pct_change"]
            for _c in _basic_cols:
                if _c in _src.columns:
                    _src[_c] = _to_db_series(_src[_c])
            if "pe" in _src.columns:
                _src["pe"] = _to_db_series(_src["pe"])
            _src["eps"] = _compute_eps_vec(_src["close"], _src["pe"].astype(float))
            if "is_close_estimated" in _src.columns:
                _src["is_close_estimated"] = _src["is_close_estimated"].fillna(False).astype(bool)
            else:
                _src["is_close_estimated"] = False
            basic_cols_out = ["date", "code"] + _basic_cols + ["pe", "eps", "is_close_estimated"]
            basic_rows = _src[[c for c in basic_cols_out if c in _src.columns]].to_dict(orient="records")
            # --- tech_rows ---
            _tech_cols = ["ma5", "ma5_ratio", "ma20", "ma60", "ma120", "ma255",
                          "ema6", "ema10", "ema20", "ema60", "ema120", "ema255"]
            tech_cols_out = ["date", "code"] + [c for c in _tech_cols if c in _src.columns]
            tech_rows = _src[tech_cols_out].to_dict(orient="records")
            # --- adj_rows ---
            _adj_cols = ["cum_split_factor", "is_split_event_day", "action_type",
                         "implied_dividend_per_share", "cum_dividend_per_share",
                         "adj_prev_close", "adj_open", "adj_high", "adj_low", "adj_close"]
            adj_cols_out = ["date", "code"] + [c for c in _adj_cols if c in _src.columns]
            adj_rows = _src[adj_cols_out].to_dict(orient="records")
            # --- liq_rows ---
            _liq_cols = ["trading_shares", "trading_amount", "rz_buy", "rz_balance",
                         "rq_sell_qty", "rq_balance_qty", "rq_balance_amt", "total_balance"]
            for _c in _liq_cols:
                if _c in _src.columns:
                    _src[_c] = _to_db_series(_src[_c])
                    _src[_c] = _src[_c].fillna(0)
            liq_cols_out = ["date", "code"] + [c for c in _liq_cols if c in _src.columns]
            liq_rows = _src[liq_cols_out].to_dict(orient="records")

            pk_cols = ["date", "code"]
            split_tables = [
                ("stats.etf_identity",         identity_rows),
                ("stats.etf_basic_stats",       basic_rows),
                ("stats.etf_tech_stats",       tech_rows),
                ("stats.etf_adjustment",        adj_rows),
                ("stats.etf_liquidity_margin",  liq_rows),
            ]
            for tbl, rows in split_tables:
                if rows:
                    n_copied, n_upserted = await copy_or_upsert_split_async(
                        conn, tbl, rows, pk_cols
                    )
                    total = n_copied + n_upserted
                    via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                          f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
                          "upsert"
                    print(f"    [DB] Inserted {total:,} rows into {tbl} via {via}", flush=True)
                else:
                    print(f"    [DB] No new rows to insert into {tbl}", flush=True)

        # ------------------------------------------------------------------
        # (7) sec_composition: insert only missing (code, snapshot_date) pairs
        # ------------------------------------------------------------------
        print("\n[7/7] Inserting ETF composition data (missing snapshots only) …", flush=True)

        comp_existing_rows = await conn.fetch(
            "SELECT DISTINCT code, snapshot_date FROM stats.sec_composition"
        )
        existing_comp_keys = {(r["code"], r["snapshot_date"]) for r in comp_existing_rows}
        print(f"    [DB] {len(existing_comp_keys):,} existing (code, snapshot_date) pairs in stats.sec_composition", flush=True)

        holdings_rows = []

        # Source 1: Full composition data (comp_long → ALL holdings)
        if comp_long is not None and len(comp_long) > 0:
            comp_eq = comp_long[comp_long["cash_sub_flag"] != "必须"].copy()
            if len(comp_eq) > 0:
                comp_eq["_shares"] = pd.to_numeric(comp_eq["shares"], errors="coerce").fillna(0.0)
                comp_eq["_w"] = comp_eq["_shares"].abs()
                n_full = 0
                for (etf_stripped, trade_date), sub in comp_eq.groupby(["etf_code", "trade_date"]):
                    if pd.isna(trade_date):
                        continue
                    total_w = float(sub["_w"].sum())
                    if total_w <= 0:
                        continue
                    code_str = str(etf_stripped).strip().zfill(6)
                    suffix = get_exchange_for_etf(code_str)
                    if not suffix:
                        continue
                    etf_code_full = f"{code_str}.{suffix}"
                    snap_date = pd.Timestamp(trade_date).date()
                    if (etf_code_full, snap_date) in existing_comp_keys:
                        continue
                    sub_sorted = sub.sort_values("_w", ascending=False).reset_index(drop=True)
                    rows_before = len(holdings_rows)
                    # Vectorized holdings: filter valid stocks, assign ranks
                    _sh = sub_sorted.copy()
                    _sh["stock_code"] = _sh["stock_code"].astype(str).str.strip()
                    _sh["sc_stripped"] = _sh["stock_code"].str.split(".").str[0].str.zfill(6)
                    _sh = _sh[
                        (_sh["sc_stripped"].str.len() == 6) &
                        _sh["sc_stripped"].str.isdigit()
                    ].copy()
                    if len(_sh) > 0:
                        _sh["rank"] = range(1, len(_sh) + 1)
                        _sh["weight_pct"] = _sh["_w"] / total_w * 100.0
                        _sh["snapshot_date"] = snap_date
                        _sh["code"] = etf_code_full
                        _sh["source_type"] = "etf"
                        _sh["stock_name"] = _sh["stock_name"].fillna("").astype(str)
                        holdings_rows.extend(
                            _sh[["snapshot_date", "code", "source_type", "rank",
                                 "stock_code", "stock_name", "weight_pct"]]
                            .to_dict(orient="records")
                        )
                    if len(holdings_rows) > rows_before:
                        n_full += 1
                print(f"    [DB] Built {len(holdings_rows):,} sec_composition rows (full comp) "
                      f"from {n_full} ETFs (skipped existing)", flush=True)

        if holdings_rows:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, "stats.sec_composition", holdings_rows,
                ["code", "snapshot_date", "rank"],
                date_column="snapshot_date",
            )
            total = n_copied + n_upserted
            via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                  f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
                  "upsert"
            print(f"    [DB] Inserted {total:,} rows into stats.sec_composition via {via}", flush=True)
        else:
            print(f"    [DB] No new rows to insert into stats.sec_composition", flush=True)

        # ---- sec_classification (type='etf'): per-ETF quality metrics ----
        avg_vol_by_code: dict = {}
        if "trading_shares" in merged_db.columns:
            avg_vol_by_code = merged_db.groupby("code")["trading_shares"].mean().to_dict()

        existing_etf_pks = await conn.fetch(
            "SELECT code, parent_index_code FROM stats.sec_classification "
            "WHERE type = 'etf'"
        )
        etf_parent_index_by_code = {
            r["code"]: r["parent_index_code"] for r in existing_etf_pks
        }

        # --- sec_classification (type='etf'): per-ETF quality metrics (vectorized) ---
        _cls = uni_df.copy()
        _cls["code"] = _cls["code"].astype(str).str.strip()
        _cls = _cls[_cls["code"].str.len() > 0].copy()
        if not _cls.empty:
            _cls["name"] = _cls["name"].fillna("").astype(str)
            _cls["n_days"] = pd.to_numeric(_cls["n_ohlcv_days"], errors="coerce").fillna(0).astype(int)
            _cls["n_margin"] = pd.to_numeric(_cls["n_margin_days"], errors="coerce").fillna(0).astype(int)
            _cls["has_margin"] = _cls["n_margin"] > 0
            _cls["avg_shares"] = _cls["code"].map(
                lambda c: float(avg_vol_by_code.get(c, 0.0) or 0.0)
            )
            # Parse dates
            _cls["first_date"] = pd.to_datetime(_cls["first_date"], errors="coerce").dt.date
            _cls["last_date"] = pd.to_datetime(_cls["last_date"], errors="coerce").dt.date
            # Base score: 100 if n_days >= 200, +50 if has_margin
            _cls["selectivity_rank_score"] = (
                (_cls["n_days"] >= 200).astype(int) * 100 +
                _cls["has_margin"].astype(int) * 50
            )
            # Parent index lookup
            _cls["parent_index_code"] = _cls["code"].map(
                lambda c: etf_parent_index_by_code.get(c, "")
            )
            _cls["type"] = "etf"
            # Sort by avg_shares and add volume-rank component
            _cls = _cls.sort_values("avg_shares", ascending=False).reset_index(drop=True)
            n_etf = len(_cls)
            if n_etf > 0:
                _vol_rank = pd.Series(range(n_etf), index=_cls.index)
                _cls["selectivity_rank_score"] += (50 * (1.0 - _vol_rank / max(n_etf, 1))).astype(int)
            _cls["n_days"] = _cls["n_days"]
            sec_classification_rows = _cls[
                ["code", "name", "type", "parent_index_code", "n_days",
                 "has_margin", "avg_shares", "first_date", "last_date",
                 "selectivity_rank_score"]
            ].to_dict(orient="records")
        else:
            sec_classification_rows = []

        if sec_classification_rows:
            inserted = await bulk_upsert_async(
                conn, "stats.sec_classification", sec_classification_rows,
                ["code", "parent_index_code"],
            )
            print(f"    [DB] Upserted {inserted:,} ETF quality rows into stats.sec_classification", flush=True)
        else:
            print(f"    [DB] No ETF quality rows to insert into stats.sec_classification", flush=True)

    finally:
        await conn.close()

    # Console summary
    print(f"\n  Theme distribution:", flush=True)
    for tid, sub in uni_df.groupby("theme_id"):
        print(f"    · {tid:<20s} {len(sub):>4d}", flush=True)

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
