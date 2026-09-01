"""Main orchestration for the SZSE + SSE + BSE stock build pipeline.

Builds combined individual-stock OHLCV + PE + margin data and inserts it
into stats.stock_identity / stock_basic_stats / stock_liquidity_margin
(missing dates only; --force rebuilds everything; --date YYYY-MM-DD
forces a single-date build — the date is ALWAYS processed even if the
DB already has it, rows are refreshed through the normal upsert paths,
no truncation / no deletes).
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta

import pandas as pd

from _common.build_commons import (
    enforce_date_force_exclusion,
    forced_date_scope,
    get_db_or_exit,
    get_db_pool_async,
    parse_date_arg,
    print_build_header,
    print_wall_time,
    truncate_table_async,
    ymd_from_filename,
    ymd_to_date,
    TODAY_STR,
)
from builds._commons.paths import (
    BSE_TREND_DIR,
    SSE_TREND_DIR,
    SSE_MARGIN_DIR,
    SZSE_ARCHIVE_DIR,
    SZSE_MARGIN_DIR,
    SZSE_TREND_DIR,
)
from builds.stock._helpers import (
    SOURCE_FILE_SETS,
    _safe_columns,
    _safe_to_datetime,
    build_missing_rows,
    estimate_missing_pe_async,
    fetch_pe_estimate_candidates,
    PE_ESTIMATE_MAX_MONTHS,
    compute_is_in_index_or_etf_async,
    ETF_WEIGHT_THRESHOLD,
)
from builds.stock.margin import build_stock_margin_df
from builds.stock.pipeline.archive import load_sse_archive, load_sse_pe
from builds.stock.pipeline.cli import normalize_code, parse_args
from builds.stock.pipeline.discovery import (
    SourceDiscovery,
    discover_sources,
    margin_loadable_dates,
)
from builds.stock.pipeline.gap_detection import (
    collect_missing_file_pairs,
    detect_missing_dates,
)
from builds.stock.pipeline.margin_gap import detect_margin_gaps
from builds.stock.pipeline.writer import (
    build_estimated_pe_rows,
    build_identity_rows,
    build_insert_rows,
    build_margin_upsert_rows,
    build_pe_upsert_rows,
    upsert_margin_only_conn,
    write_basic_stats_ohlcv,
    write_identity,
    write_liquidity_margin,
    write_pe_only_conn,
)


async def purge_for_force(conn, code_filter: str | None) -> None:
    """--force: truncate target tables (or delete only the --code's rows)."""
    if code_filter:
        print(f"    [DB] Force mode for code {code_filter}: deleting existing rows for this code", flush=True)
        await conn.execute(
            "DELETE FROM stats.stock_liquidity_margin WHERE code = $1",
            code_filter,
        )
        await conn.execute(
            "DELETE FROM stats.stock_tech_stats WHERE code = $1",
            code_filter,
        )
        await conn.execute(
            "DELETE FROM stats.stock_basic_stats WHERE code = $1",
            code_filter,
        )
        # stock_identity rows are FK-referenced by e.g. stock_intraday_5min
        # (deleting raises ForeignKeyViolation) and are dimension-like —
        # the build re-upserts them right before basic_stats anyway.
        print("    [DB] Skipping stock_identity delete (FK-referenced; "
              "re-upserted during insert)", flush=True)
    else:
        print("    [DB] Force mode: truncating existing tables", flush=True)
        await truncate_table_async(conn, "stats.stock_liquidity_margin")
        await truncate_table_async(conn, "stats.stock_basic_stats")
        await truncate_table_async(conn, "stats.stock_identity")


async def _run_tech_stats_step(conn, args, code_filter: str | None,
                               forced: date | None = None) -> None:
    """Run tech stats only when behind basic_stats (single cheap
    max-date check) — a no-op tech-stats run costs ~15s of lookback
    loading, so skip it when there is nothing new to compute.

    --date mode (forced): bypass the max-date skip and recompute ONLY
    the forced date's rows via the runner's target_dates mechanism —
    never a full --force-style history recompute."""
    if code_filter:
        # In single-code mode, skip full tech-stats scan (it processes
        # all codes). The target code's existing tech stats remain valid.
        print(f"    [TECH-STATS] Skipped in single-code mode ({code_filter})", flush=True)
        return
    if not args.force and forced is None:
        row = await conn.fetchrow(
            "SELECT "
            "  (SELECT MAX(date) FROM stats.stock_basic_stats) AS max_basic, "
            "  (SELECT MAX(date) FROM stats.stock_tech_stats)  AS max_tech"
        )
        if row and row["max_basic"] is not None \
                and row["max_tech"] is not None \
                and row["max_basic"] <= row["max_tech"]:
            print("    [TECH-STATS] Up to date "
                  f"(max date {row['max_tech']}) — skipped", flush=True)
            return
    print("\n[5/5] Computing stock tech stats (MA/EMA) …", flush=True)
    from builds.stock.tech_stats import run_tech_stats_chunked
    tech_total = await run_tech_stats_chunked(
        conn, force=args.force, chunk_size=500,
        target_dates={forced} if forced is not None else None,
    )
    print(f"    [TECH-STATS] Total rows upserted into stats.stock_tech_stats: "
          f"{tech_total:,}", flush=True)


def _update_history_range(combined: pd.DataFrame,
                          history_start: date | None,
                          history_end: date | None) -> tuple[date | None, date | None]:
    """Widen the history range with the combined frame's date span."""
    if len(combined) == 0:
        return history_start, history_end
    # GPU-safe min/max: strftime on GPU then string min/max (scalar
    # Timestamp.date() has no cudf fast path)
    date_strs = combined["date"].dt.strftime("%Y-%m-%d")
    combined_min = date.fromisoformat(date_strs.min())
    if history_start is None or combined_min < history_start:
        history_start = combined_min
    combined_max = date.fromisoformat(date_strs.max())
    if history_end is None or combined_max > history_end:
        history_end = combined_max
    return history_start, history_end


async def main() -> None:
    args = parse_args()
    enforce_date_force_exclusion(args)
    forced = parse_date_arg(args.date)
    code_filter: str | None = normalize_code(args.code)
    t0 = time.time()
    if forced is not None:
        print(f"[DATE MODE] Forced single-date build: {forced}", flush=True)
        # Restrict discovery + the SSE archive loader to the single date
        # BEFORE any source scanning happens below.
        args.start_date = forced.isoformat()
        args.end_date = forced.isoformat()
    if code_filter:
        print(f"    [CODE FILTER] Restricting build to single stock: {code_filter}", flush=True)

    print_build_header(
        "SZSE + SSE + BSE STOCK BUILDER  ·  missing-dates-only → DATABASE",
        **{
            "SZSE Archive dir": SZSE_ARCHIVE_DIR,
            "SZSE Trend dir":   SZSE_TREND_DIR,
            "SSE Trend dir":    SSE_TREND_DIR,
            "BSE Trend dir":    BSE_TREND_DIR,
            "Date range":       f"{args.start_date or '(all)'} → {args.end_date or '(all)'}",
            "Code filter":      code_filter or "(none — all stocks)",
            "Today":            TODAY_STR,
        }
    )

    # ------------------------------------------------------------------
    # 1. Discover all source CSV files in date range
    # ------------------------------------------------------------------
    print("\n[1/4] Discovering source CSV files …", flush=True)
    # Exchange-dir rule: a single --code stock can only ever appear in its
    # own exchange's source dirs, so cross-exchange files must never be
    # read. .SZ → SZSE dirs (szse_stock_ / szse_trend_stock_ + SZSE margin),
    # .SS → SSE dirs (+ SSE margin), .BJ → BSE dir only (no margin detail
    # exists for BSE). OHLCV pruning happens inside discover_sources (the
    # per-file non-empty peek is per-file I/O); margin lists are pruned here.
    code_suffix = code_filter[-3:] if code_filter else None  # ".SZ"/".SS"/".BJ"
    disc: SourceDiscovery = discover_sources(
        args.start_date, args.end_date, args.limit, code_suffix,
    )

    if code_suffix:
        ex_name = {"SZ": "SZSE", "SS": "SSE", "BJ": "BSE"}.get(code_suffix.lstrip("."), "?")
        if code_suffix == ".SZ":
            disc.sse_margin_files = []
        elif code_suffix == ".SS":
            disc.szse_margin_files = []
        else:  # ".BJ": neither market's margin details carry BSE codes
            disc.sse_margin_files = []
            disc.szse_margin_files = []
        print(f"    [CODE FILTER] Exchange-dir rule: {code_filter} ({ex_name}) → "
              f"{len(disc.all_files)} source files in scope "
              f"(cross-exchange dirs excluded)", flush=True)

    print(f"    → {len(disc.all_files)} source CSV files in range", flush=True)
    if not disc.all_files:
        print("    [FATAL] No source CSVs found", flush=True)
        raise SystemExit(1)

    n_unloadable = len(disc.available_dates) - len(disc.loadable_dates)
    print(f"    → {len(disc.available_dates)} unique dates available in source files "
          f"({len(disc.loadable_dates)} loadable, {n_unloadable} holiday/placeholder)",
          flush=True)
    print(f"    → Margin: {len(disc.szse_margin_files)} szse + {len(disc.sse_margin_files)} sse files",
          flush=True)

    history_start: date | None = min(disc.available_dates) if disc.available_dates else None
    history_end: date | None = max(disc.available_dates) if disc.available_dates else None
    if forced is not None:
        # --date clamps discovery (and thus this range) to the single day,
        # which would starve the step-4d PE estimation of any baseline:
        # the estimator only looks STRICTLY before the target date. Widen
        # the start to the estimator's own lookback window — rows are
        # still only WRITTEN for the forced date.
        history_start = forced - timedelta(days=31 * PE_ESTIMATE_MAX_MONTHS)
    if history_start and history_end:
        print(f"    → history date range: {history_start} → {history_end}",
              flush=True)

    # ------------------------------------------------------------------
    # 2. Connect to DB and find missing dates
    # ------------------------------------------------------------------
    print("\n[2/4] Connecting to database and detecting missing dates …", flush=True)
    conn = await get_db_or_exit()
    pool = await get_db_pool_async(min_size=1, max_size=4)

    try:
        if args.force:
            await purge_for_force(conn, code_filter)
        if forced is not None:
            # --date: bypass missing-date detection entirely — the forced
            # date is ALWAYS processed. Rows already in the DB are
            # refreshed through the normal upsert write paths; the
            # --force purge above can never run here (mutually exclusive).
            missing_dates = forced_date_scope(disc.loadable_dates, forced)
        else:
            missing_dates = await detect_missing_dates(
                conn, disc.loadable_dates, code_filter, args.force
            )

        # ------------------------------------------------------------------
        # 3. Filter source files to only missing dates and build rows
        # ------------------------------------------------------------------
        combined = pd.DataFrame()
        if missing_dates:
            print(f"\n[3/4] Reading source CSVs for {len(missing_dates)} missing dates …", flush=True)
            missing_file_pairs = await collect_missing_file_pairs(
                conn, disc.all_files, args.force, code_filter,
                force_dates=missing_dates if forced is not None else None,
            )
            print(f"    → {len(missing_file_pairs)} source CSV files to read (all suffixes)", flush=True)
            combined = build_missing_rows(missing_file_pairs, verbose=True, code=code_filter)

        # ------------------------------------------------------------------
        # 3b. Load SSE archive historical OHLCV (per-stock {code}_trend.csv)
        #     — incremental: file mtime + per-code DB max date
        # ------------------------------------------------------------------
        # --date: pass force=True so the loader's mtime/per-code DB-max
        # gating cannot skip the forced date's rows. The load stays
        # restricted to the single date via start/end (rows are filtered
        # to that day inside the loader) — pure upsert refresh downstream,
        # no truncation.
        archive_df = await load_sse_archive(
            conn, args.start_date, args.end_date, args.limit,
            args.force or forced is not None, code_filter,
        )
        if len(archive_df) > 0:
            if len(combined) > 0:
                combined = pd.concat([combined, archive_df], ignore_index=True)
            else:
                combined = archive_df
            combined = combined.sort_values(["date", "code"]).reset_index(drop=True)
            combined = combined.drop_duplicates(
                subset=["date", "code"], keep="last"
            ).reset_index(drop=True)

        history_start, history_end = _update_history_range(combined, history_start, history_end)

        # ------------------------------------------------------------------
        # 3c. (removed) SSE PE no longer merges into the combined frame —
        #     it is an INDEPENDENT pass after the DB writes (step 4c), with
        #     latest-missing-dates gating inside _read_sse_pe_files
        # ------------------------------------------------------------------

        # ------------------------------------------------------------------
        # 3d. Load stock margin (融资融券)
        # ------------------------------------------------------------------
        margin_avail_dates = margin_loadable_dates(
            disc.szse_margin_files + disc.sse_margin_files
        )
        if forced is not None:
            # --date: the gap checks below scan margin_available_dates and
            # UNION their findings into the target set — left unrestricted
            # they would pull historical repair dates into a single-date
            # run. Restrict the check window to the forced date;
            # missing_dates already guarantees the date is targeted.
            margin_avail_dates &= missing_dates
        mg = await detect_margin_gaps(
            conn, margin_avail_dates, missing_dates, code_filter, args.force,
            disc.szse_margin_files, disc.sse_margin_files,
        )
        margin_target_dates = mg.target_dates

        # Re-read OHLCV source CSVs ONLY for dates where stock_identity has
        # a row but liquidity_margin has none (partial exports). The
        # independent margin pass covers every other case: zero-liquidity
        # dates get their rows from margin-only upserts with trading
        # defaults, so reading snapshots for them is pure waste.
        needs_ohlcv_backfill = bool(mg.missing_liq_dates)
        if needs_ohlcv_backfill and margin_target_dates:
            print(f"\n    [MARGIN-BACKFILL] Loading OHLCV source CSVs for "
                  f"{len(mg.missing_liq_dates)} dates missing from "
                  f"liquidity_margin …",
                  flush=True)
            # Reuse the discovery result: already date-ranged AND pruned to
            # the --code exchange scope (if set) by the exchange-dir rule.
            all_source_files = disc.all_files
            backfill_file_pairs: list[tuple] = []
            for path, market, _suffix in all_source_files:
                for _dir, _pat, prefix, _mkt, _sfx in SOURCE_FILE_SETS:
                    ymd = ymd_from_filename(path, prefix)
                    if ymd:
                        d = ymd_to_date(ymd)
                        if d and d in margin_target_dates:
                            backfill_file_pairs.append((path, market))
                            break
            print(f"    → {len(backfill_file_pairs)} source CSV files to read "
                  f"for margin backfill", flush=True)
            ohlcv_backfill = build_missing_rows(
                backfill_file_pairs, verbose=True, code=code_filter,
            )
            if len(ohlcv_backfill) > 0:
                if len(combined) > 0:
                    combined = pd.concat(
                        [combined, ohlcv_backfill], ignore_index=True
                    )
                    combined["date"] = _safe_to_datetime(combined["date"])
                    combined = combined.sort_values(
                        ["date", "code", "close"], na_position="first"
                    ).reset_index(drop=True)
                    combined = combined.drop_duplicates(
                        subset=["date", "code"], keep="last"
                    ).reset_index(drop=True)
                else:
                    combined = ohlcv_backfill
                n_recovered = len(ohlcv_backfill)
                backfill_cols = _safe_columns(ohlcv_backfill)
                had_close = ohlcv_backfill["close"].notna().sum() \
                    if "close" in backfill_cols else 0
                had_amount = ohlcv_backfill["trading_amount"].notna().sum() \
                    if "trading_amount" in backfill_cols else 0
                print(f"    [MARGIN-BACKFILL] Recovered {n_recovered:,} rows "
                      f"({had_close:,} with OHLCV close, {had_amount:,} "
                      f"with trading_amount). "
                      f"Combined now has {len(combined):,} rows.",
                      flush=True)

        if len(combined) == 0 and not margin_target_dates:
            print("    [INFO] No new rows to insert", flush=True)
            await _run_tech_stats_step(conn, args, code_filter, forced)
            print_wall_time(t0)
            return

        if margin_target_dates:
            print(f"\n    Loading stock margin from SZSE + SSE detail CSVs "
                  f"({len(margin_target_dates)} target dates) …", flush=True)
            if args.force:
                margin_file_sets = {
                    "szse": disc.szse_margin_files,
                    "sse":  disc.sse_margin_files,
                }
            else:
                missing_szse_margin = [
                    f for f in disc.szse_margin_files
                    if ymd_from_filename(f, "szse_margin_detail_")
                    and ymd_to_date(ymd_from_filename(f, "szse_margin_detail_"))
                    in margin_target_dates
                ]
                missing_sse_margin = [
                    f for f in disc.sse_margin_files
                    if ymd_from_filename(f, "sse_margin_detail_")
                    and ymd_to_date(ymd_from_filename(f, "sse_margin_detail_"))
                    in margin_target_dates
                ]
                margin_file_sets = {
                    "szse": missing_szse_margin,
                    "sse":  missing_sse_margin,
                }
            margin_df = build_stock_margin_df(
                SZSE_MARGIN_DIR, SSE_MARGIN_DIR,
                verbose=True, margin_files=margin_file_sets,
                code=code_filter,
            )
        else:
            margin_df = None

        # Filter to target code if --code is set (mask results are fresh
        # frames; DB emission is index-blind — no reindex)
        if code_filter and len(combined) > 0:
            n_before = len(combined)
            combined = combined[combined["code"] == code_filter]
            print(f"    [CODE FILTER] Filtered combined from {n_before:,} → {len(combined):,} rows for code {code_filter}", flush=True)
        if code_filter and margin_df is not None and len(margin_df) > 0:
            margin_df = margin_df[margin_df["code"] == code_filter]

        # Stock source loaders never estimate close — default the flag to
        # False when no loader produced it (mirrors builds/etf pattern).
        if len(combined) > 0 and "is_close_estimated" not in _safe_columns(combined):
            combined["is_close_estimated"] = False

        # ------------------------------------------------------------------
        # 4. Insert into database
        # ------------------------------------------------------------------
        print(f"\n[4/4] Inserting data to database …", flush=True)

        # Single explicit GPU→host transfer at the DB boundary: all row
        # building below ends in Python objects for asyncpg anyway, and
        # .dt.date on a cudf-backed frame falls back per element
        # (Timestamp.date has no GPU fast path). On host pandas it is a
        # plain vectorized conversion.
        n_margin_rows = len(margin_df) if margin_df is not None else 0
        if len(combined) > 0:
            # May already be host pandas when every loader took a CPU path
            # (e.g. margin-only recovery from plain CSV reads) — hasattr
            # guard, not isinstance, since cudf.pandas proxies blur types
            combined_db = combined.to_pandas() if hasattr(combined, "to_pandas") else combined
            combined_db["date"] = combined_db["date"].dt.date
            combined_db = combined_db.drop_duplicates(subset=["date", "code"], keep="last")
        else:
            combined_db = None

        if (combined_db is None or len(combined_db) == 0) and n_margin_rows == 0:
            print("    [INFO] No new OHLCV/PE/margin rows to insert "
                  "(all missing dates are holidays/empty)", flush=True)
            await _run_tech_stats_step(conn, args, code_filter, forced)
            print_wall_time(t0)
            return

        rows = None
        if combined_db is not None and len(combined_db) > 0:
            # --- 4a. Build & insert identity rows ---
            batch_dates = set(combined_db["date"].tolist())
            print(f"    [ETF] Resolving is_in_index_or_etf for {len(batch_dates)} dates from "
                  f"sec_composition (source_type='etf', weight_pct > {ETF_WEIGHT_THRESHOLD}) …",
                  flush=True)
            etf_membership = await compute_is_in_index_or_etf_async(conn, batch_dates)
            identity_rows, n_in_etf = build_identity_rows(combined_db, etf_membership)
            print(f"    [ETF] {n_in_etf:,} / {len(identity_rows):,} rows flagged "
                  f"is_in_index_or_etf=true in this batch", flush=True)
            await write_identity(conn, identity_rows)

            rows = build_insert_rows(combined_db)
            print(f"    [BUILD] Snapshot-PE rows: {rows.n_actual:,} | "
                  f"Rows without pe (estimation candidates): {rows.n_missing:,}",
                  flush=True)

            # --- Parallel DB writes (OHLCV-scoped basic_stats + liquidity) ---
            print(f"\n    [POOL] Running basic_stats(OHLCV) + liquidity_margin writes in parallel "
                  f"(pool size={pool.get_size()}) …", flush=True)
            await asyncio.gather(
                write_basic_stats_ohlcv(pool, rows.ov_rows),
                write_liquidity_margin(pool, rows.liq_rows),
            )
        else:
            print("    [MARGIN-ONLY] No OHLCV/PE rows in scope — margin pass only",
                  flush=True)

        # --- 4b. Independent margin pass (column-scoped upsert) ---
        # Writes ONLY the 6 margin columns; self-seeds identity keys; runs
        # after all parallel writes so identity/FK state is final.
        if n_margin_rows > 0:
            await upsert_margin_only_conn(
                conn, build_margin_upsert_rows(margin_df)
            )
        elif rows is not None:
            print("    [DB] No new margin data to upsert", flush=True)

        # --- 4c. Independent SSE PE pass (column-scoped, latest-missing-dates)
        # Reads {code}_pe.csv tail rows beyond each code's DB max PE date and
        # upserts pe/eps/is_pe_estimated ONLY. Runs BEFORE estimation so fresh
        # actual PEs become baselines and are never overwritten by estimates.
        sse_pe_df = await load_sse_pe(conn, args.force, code_filter)
        if len(sse_pe_df) > 0:
            file_pe_rows = build_pe_upsert_rows(sse_pe_df)
            await write_pe_only_conn(conn, file_pe_rows, "SSE PE files")
        elif rows is not None:
            print("    [PE] No new SSE PE file data beyond DB max", flush=True)
        if rows is not None and rows.snapshot_pe_rows:
            await write_pe_only_conn(conn, rows.snapshot_pe_rows, "snapshot pe")

        # --- 4d. Estimate still-missing PE from the DB state ---
        # Source of truth is the DATABASE (not the frame): rows that just
        # received an actual PE via the passes above are excluded here.
        batch_dates: list[date] = []
        if combined_db is not None and len(combined_db) > 0:
            batch_dates = sorted(set(combined_db["date"].tolist()))
        missing_rows = await fetch_pe_estimate_candidates(conn, batch_dates)
        if missing_rows:
            print(f"    [ESTIMATE] Looking up last actual PE for {len(missing_rows):,} "
                  f"DB rows lacking pe (history range: {history_start} → {history_end}) …",
                  flush=True)
            estimated_pe_map = await estimate_missing_pe_async(
                conn, missing_rows, history_start, history_end
            )
            n_estimated = len(estimated_pe_map)
            n_no_baseline = len(missing_rows) - n_estimated
            estimated_basic_stats_rows = build_estimated_pe_rows(
                missing_rows, estimated_pe_map
            )
            await write_pe_only_conn(
                conn, estimated_basic_stats_rows, "estimated"
            )
            print(f"    [ESTIMATE] Estimated PE for {n_estimated:,} rows "
                  f"(is_pe_estimated=true) | {n_no_baseline:,} rows have no "
                  f"usable prior actual PE within {PE_ESTIMATE_MAX_MONTHS} "
                  f"months — pe stays NULL (is_pe_estimated=false)", flush=True)
        else:
            print(f"    [ESTIMATE] No rows lacking pe among ingested dates",
                  flush=True)

        # ------------------------------------------------------------------
        # 5. Compute tech stats (MA/EMA) for all stocks
        # ------------------------------------------------------------------
        await _run_tech_stats_step(conn, args, code_filter, forced)

    finally:
        await conn.close()
        await pool.close()

    print_wall_time(t0)
