"""Pipeline orchestrator — glues the staged ETF build in order."""
from __future__ import annotations

import time

import numpy as np

from _common.build_commons import (
    get_db_or_exit, print_build_header, print_wall_time, TODAY_STR,
    forced_date_scope,
)
from _common.df_utils import host_array

from builds.etf.paths import (
    SZSE_ARCHIVE_DIR, SZSE_TREND_DIR,
    SZSE_MARGIN_DIR, COMP_DIR, HUATAI_COMP_DIR,
)
from builds.etf.pipeline.discover import discover_source_files
from builds.etf.pipeline.scope import (
    purge_existing_data,
    fetch_existing_data_keys,
    fetch_per_code_day_stats,
    compute_dates_to_read,
    fetch_margin_stale_dates,
)
from builds.etf.pipeline.load import load_source_frames, _csv_has_data_row
from builds.etf.pipeline.features import prepare_features, load_composition
from builds.etf.pipeline.pe_scope import build_pe_scope
from builds.etf.pipeline.universe import build_universe
from builds.etf.pipeline.writes import filter_missing_rows, write_split_tables
from builds.etf.pipeline.composition_write import insert_composition
from builds.etf.pipeline.quality import upsert_quality_metrics
from builds._commons.board_map import stage_stock_boards, refresh_board_mix

import logging
logger = logging.getLogger(__name__)


async def run(args) -> None:
    """Full ETF build: discovery → scope → load → features → PE → writes.

    With ``--date`` (args.forced_date): single-date forced rebuild — the
    DB missing-date skip is bypassed, the forced date is ALWAYS processed,
    and its existing rows are refreshed through the normal upsert write
    paths (no truncation, no deletes).
    """
    code_filter = getattr(args, "resolved_code", None)
    if code_filter:
        logger.info(f"    [CODE FILTER] Restricting build to single ETF: {code_filter}")

    forced = getattr(args, "forced_date", None)
    if forced:
        logger.info(f"[DATE MODE] Forced single-date build: {forced}")

    t0 = time.time()
    header_fields = {
        "SZSE Archive dir": SZSE_ARCHIVE_DIR,
        "SZSE Trend dir":   SZSE_TREND_DIR,
        "Margin dir":       SZSE_MARGIN_DIR,
        "Composition dir":  COMP_DIR,
        "Huatai comp dir":  HUATAI_COMP_DIR,
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
    logger.info("\n[1/7] Discovering source CSV files …")
    files, available_dates = discover_source_files()

    # (2) Connect to DB and find missing dates
    logger.info("\n[2/7] Connecting to database and detecting missing dates …")
    conn = await get_db_or_exit()
    try:
        if args.force:
            await purge_existing_data(conn, code_filter)
            existing_keys: set = set()
            existing_dates: set = set()
        else:
            existing_keys, existing_dates = await fetch_existing_data_keys(
                conn, code_filter, force=False)

        logger.info(f"    [DB] {len(existing_keys):,} existing (date, code) pairs in stats.etf_basic_stats")
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
        logger.info(f"    [DB] {len(missing_ohlcv_dates)} dates missing "
              f"(out of {len(available_dates)} available)")

        # (2b) Margin-stale repair dates: incremental mode only (force
        # purges everything; --date refreshes its own date). Dates whose
        # stored margin is all-NULL/zero are re-read + re-upserted once
        # their margin detail files actually carry data — a late-arriving
        # CSV repairs the date instead of the zeros staying baked forever.
        margin_stale_dates: set = set()
        if not args.force and not forced:
            stale_candidates = await fetch_margin_stale_dates(
                conn, available_dates, code_filter)
            if stale_candidates:
                stale_ymd = {d.strftime("%Y%m%d") for d in stale_candidates}
                margin_files = files.get("szse", []) + files.get("sse", [])
                data_dates: set = set()
                for f in margin_files:
                    base = f.replace("\\", "/").rsplit("/", 1)[-1]
                    if any(ymd in base for ymd in stale_ymd) \
                            and _csv_has_data_row(f):
                        for ymd in stale_ymd:
                            if ymd in base:
                                data_dates.add(ymd)
                margin_stale_dates = {
                    d for d in stale_candidates
                    if d.strftime("%Y%m%d") in data_dates
                }
                if margin_stale_dates:
                    logger.info(f"    [DB] {len(margin_stale_dates)} margin-stale dates "
                          f"have CSV data — scheduled for margin repair")

        # (3) Read ONLY missing-date source CSVs + query DB for history
        ohlcv_df, margin_df, adj_seeds = await load_source_frames(
            conn, files,
            force=args.force, code_filter=code_filter,
            dates_to_read=missing_ohlcv_dates | recent_refresh_dates
            | margin_stale_dates,
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
        # margin-stale rows: host-side datetime64[D] isin (same idiom as
        # pe_scope's forced_mask — no per-row pandas comparisons).
        margin_stale_mask = None
        if margin_stale_dates:
            dates_d = np.asarray(host_array(merged["date"])).astype("datetime64[D]")
            ref = np.array(sorted(margin_stale_dates), dtype="datetime64[D]")
            margin_stale_mask = np.isin(dates_d, ref)
        merged_missing, _n_resync = filter_missing_rows(
            merged,
            exists=pe.exists, split_mask=pe.split_mask, in_range=pe.in_range,
            pe_null_hit=pe.pe_null_hit, forced_mask=pe.forced_mask,
            margin_stale_mask=margin_stale_mask,
        )
        await write_split_tables(conn, merged_missing, force=args.force)

        # (7) sec_composition: insert only missing (code, snapshot_date) pairs
        await insert_composition(conn, comp_long, code_filter, forced_date=forced)

        # Post-step: sec_classification quality metrics
        await upsert_quality_metrics(conn, uni_df, merged)

        # Post-step: stats.sec_board_map etf slice — composition-weighted
        # board mix per ETF (tracking-index fallback for codes without own
        # snapshots). Must run AFTER insert_composition: mixes read the
        # latest snapshots this build may have just added.
        await stage_stock_boards(conn)
        await refresh_board_mix(conn, "etf", force=args.force)

        # (8) OPT-IN (--analyze): refresh planner statistics. Bulk writes
        # leave column stats stale until autovacuum catches up (hours on
        # these tables), and value-blind prepared-statement readers
        # (node-pg plans before bind values) then misestimate into whole-
        # table merge/seq scans — the 2026-09-29 v_etf_margin exact-code
        # 3.5s UI regression (the planner scanned the full 1M-row
        # etf_liquidity_margin / etf_adjustment partitions to return 42
        # rows). Same convention as builds.stock --analysis.
        if getattr(args, "analyze", False):
            from _common.db_commons import analyze_tables_async
            durations = await analyze_tables_async(conn, (
                "stats.etf_identity",
                "stats.etf_basic_stats",
                "stats.etf_tech_stats",
                "stats.etf_adjustment",
                "stats.etf_liquidity_margin",
            ))
            if durations:
                logger.info("    [ANALYZE] refreshed: "
                      + ", ".join(f"{t} ({d:.1f}s)" for t, d in durations.items()))
    finally:
        await conn.close()

    # Console summary
    logger.info("\n  Theme distribution:")
    for tid, sub in uni_df.groupby("theme_id"):
        logger.info(f"    · {tid:<20s} {len(sub):>4d}")

    print_wall_time(t0)
