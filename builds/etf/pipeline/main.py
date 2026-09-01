"""Pipeline orchestrator — glues the staged ETF build in order."""
from __future__ import annotations

import time

from _common.build_commons import (
    get_db_or_exit, print_build_header, print_wall_time, TODAY_STR,
    forced_date_scope,
)

from builds.etf.paths import (
    SZSE_ARCHIVE_DIR, SZSE_TREND_DIR,
    SZSE_MARGIN_DIR, COMP_DIR,
)
from builds.etf.pipeline.discover import discover_source_files
from builds.etf.pipeline.scope import (
    purge_existing_data,
    fetch_existing_identity_keys,
    fetch_per_code_day_stats,
    compute_dates_to_read,
)
from builds.etf.pipeline.load import load_source_frames
from builds.etf.pipeline.features import prepare_features, load_composition
from builds.etf.pipeline.pe_scope import build_pe_scope
from builds.etf.pipeline.universe import build_universe
from builds.etf.pipeline.writes import filter_missing_rows, write_split_tables
from builds.etf.pipeline.composition_write import insert_composition
from builds.etf.pipeline.quality import upsert_quality_metrics


async def run(args) -> None:
    """Full ETF build: discovery → scope → load → features → PE → writes.

    With ``--date`` (args.forced_date): single-date forced rebuild — the
    DB missing-date skip is bypassed, the forced date is ALWAYS processed,
    and its existing rows are refreshed through the normal upsert write
    paths (no truncation, no deletes).
    """
    code_filter = getattr(args, "resolved_code", None)
    if code_filter:
        print(f"    [CODE FILTER] Restricting build to single ETF: {code_filter}", flush=True)

    forced = getattr(args, "forced_date", None)
    if forced:
        print(f"[DATE MODE] Forced single-date build: {forced}", flush=True)

    t0 = time.time()
    header_fields = {
        "SZSE Archive dir": SZSE_ARCHIVE_DIR,
        "SZSE Trend dir":   SZSE_TREND_DIR,
        "Margin dir":       SZSE_MARGIN_DIR,
        "Composition dir":  COMP_DIR,
        "Date range":       f"{args.start_date or '(all)'} → {args.end_date or '(all)'}",
        "Code filter":      code_filter or "(none — all ETFs)",
        "Today":            TODAY_STR,
    }
    if forced:
        header_fields["Forced date"] = str(forced)
    print_build_header(
        "BUILD SZSE + SSE ETF + MARGIN + COMPOSITION + PE  ·  missing-data-only → DATABASE",
        **header_fields,
    )

    # (1) Discover source files (fast — filenames only, no reading)
    print("\n[1/7] Discovering source CSV files …", flush=True)
    files, available_dates = discover_source_files()

    # (2) Connect to DB and find missing dates
    print("\n[2/7] Connecting to database and detecting missing dates …", flush=True)
    conn = await get_db_or_exit()
    try:
        if args.force:
            await purge_existing_data(conn, code_filter)
            existing_keys: set = set()
            existing_dates: set = set()
        else:
            existing_keys, existing_dates = await fetch_existing_identity_keys(
                conn, code_filter, force=False)

        print(f"    [DB] {len(existing_keys):,} existing (date, code) pairs in stats.etf_identity", flush=True)
        if args.force:
            missing_ohlcv_dates = available_dates - existing_dates
            recent_refresh_dates: set = set()
            day_stats = None
        elif forced:
            # --date: bypass the DB missing-date skip — the forced date is
            # always processed (exits 1 if it has no source files). Tables
            # are NOT purged, so the B3 day-stats fetch still applies.
            missing_ohlcv_dates = forced_date_scope(available_dates, forced)
            recent_refresh_dates = set()
            day_stats = await fetch_per_code_day_stats(conn, code_filter)
        else:
            missing_ohlcv_dates, recent_refresh_dates = compute_dates_to_read(
                available_dates, existing_dates)
            # B3 correction source: true per-code first/last/day counts for
            # build_universe (merged is window-truncated on this path).
            day_stats = await fetch_per_code_day_stats(conn, code_filter)
        print(f"    [DB] {len(missing_ohlcv_dates)} dates missing "
              f"(out of {len(available_dates)} available)", flush=True)

        # (3) Read ONLY missing-date source CSVs + query DB for history
        ohlcv_df, margin_df, adj_seeds = await load_source_frames(
            conn, files,
            force=args.force, code_filter=code_filter,
            dates_to_read=missing_ohlcv_dates | recent_refresh_dates,
        )

        # (4) Merge OHLCV + margin, apply corp-action adjustment + MAs
        merged = prepare_features(ohlcv_df, margin_df, adj_seeds)

        # (4b) Build composition (for sec_composition insertion + PE)
        # — GATE-FIRST: existing snapshots fetched from the DB before any
        # composition CSV is read (B1).
        comp_long, comp_universe = await load_composition(
            conn, code_filter, args.force, forced_date=forced)

        # (5) Compute ETF PE + shared write-scope masks
        pe = await build_pe_scope(
            conn, merged, comp_long,
            code_filter=code_filter, force=args.force,
            start_date=args.start_date, end_date=args.end_date,
            existing_keys=existing_keys, forced_date=forced,
        )
        merged = pe.merged

        # (5b) Build universe (for sec_classification stats — full merged data)
        uni_df = build_universe(merged, comp_universe, day_stats, pe.exists)

        # (6) Filter to write candidates and insert OHLCV/margin tables
        merged_missing, _n_resync = filter_missing_rows(
            merged,
            exists=pe.exists, split_mask=pe.split_mask, in_range=pe.in_range,
            pe_null_hit=pe.pe_null_hit, forced_mask=pe.forced_mask,
        )
        await write_split_tables(conn, merged_missing, force=args.force)

        # (7) sec_composition: insert only missing (code, snapshot_date) pairs
        await insert_composition(conn, comp_long, code_filter, forced_date=forced)

        # Post-step: sec_classification quality metrics
        await upsert_quality_metrics(conn, uni_df, merged)
    finally:
        await conn.close()

    # Console summary
    print("\n  Theme distribution:", flush=True)
    for tid, sub in uni_df.groupby("theme_id"):
        print(f"    · {tid:<20s} {len(sub):>4d}", flush=True)

    print_wall_time(t0)
