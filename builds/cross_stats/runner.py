"""builds.cross_stats runner — orchestrates the pair + industry grains.

``run_cross_stats(conn, *, force)``:
  0. Composition preflight gate: stats.sec_composition (source_type=
     'index') must hold holdings — the whole cross primitive is derived
     from compositions. Missing → exit(1) with the exact remediation
     command (run `python -m builds.index` phase 1 first).
  1. Force: TRUNCATE stats.cross_stats (+ dates map; drop the secondary
     (sec_type, date) index — post-created after the bulk COPY).
     Incremental: detect missing dates (stats.index_identity vs the tiny
     dates map) and early-return when up to date.
  2. PAIR grain: fetch weights/closes/ETF amounts → build_and_insert
     (chunked COPY; corr OFF — see run_corr_update).
  3. INDUSTRY grain: one INSERT...SELECT (FULL after truncate /
     INCREMENTAL for target dates + catch-up dates where pair rows
     exist but industry rows do not).
  4. Register written dates in the map + post-create the secondary index,
     then refresh the API-facing code-summary rollup
     (stats.cross_stats_code_summary).

``run_corr_update(conn)`` (the ``--corr`` sub-command) recomputes
corr_20d/60d/255d for grid dates and upserts them onto existing rows
(payload = 4 PK cols + 3 corr cols; base columns never touched).

Self-manages target_dates (like the migrated sec_alloc producer): the
downstream attributions step requires cross_stats to be fully current
for its target dates regardless of the caller's state.
"""
from __future__ import annotations

import datetime
import sys
import time
from typing import Optional, Set

import pandas as pd

from _common.build_commons import (
    truncate_table_async,
    fetch_codes_with_recent_data_async,
    RECENT_TRADING_DAYS,
    recent_trading_day_cutoff,
)

from builds.cross_stats._perf import timed, print_declared_blockers
from builds.cross_stats.config import (
    CORR_WINDOWS,
    DATES_MAP_TABLE,
    SEC_TYPE_DATE_INDEX,
    SEC_TYPE_DATE_INDEX_SQL,
    SET_WORK_MEM_SQL,
    SUMMARY_TABLE,
    TABLE,
)
from builds.cross_stats.fetch import (
    fetch_codes_with_composition,
    fetch_shared_weights,
    fetch_index_closes,
    fetch_index_subject_closes,
    fetch_etf_amount_by_index,
)
from builds.cross_stats.compute import build_and_insert
from builds.cross_stats.compute._gpu_corr import fetch_corr_grid_dates
from builds.cross_stats.compute._lookback import LOOKBACK_TRADING_DAYS
from builds.cross_stats._industry import (
    INDUSTRY_INSERT_SQL_FULL,
    INDUSTRY_INSERT_SQL_INCREMENTAL,
)
from builds.cross_stats._summary import (
    refresh_code_summary,
    summary_is_stale,
)


# ---------------------------------------------------------------------------
#  Preflight gates + dates map
# ---------------------------------------------------------------------------
async def check_composition_present(conn) -> int:
    """Composition preflight gate (Phase-0 of the refactor plan).

    Returns the number of index holdings rows. Exits(1) when zero —
    every shared weight here derives from stats.sec_composition.
    """
    n = await conn.fetchval("""
        SELECT count(*) FROM stats.sec_composition
        WHERE source_type = 'index' AND stock_code IS NOT NULL
    """)
    n = int(n or 0)
    if n == 0:
        print(
            "\n[COMPOSITION GATE] stats.sec_composition has NO index "
            "holdings (source_type='index', stock_code IS NOT NULL).\n"
            "  cross_stats is entirely composition-derived — run the "
            "composition build FIRST:\n"
            "    wsl -d Ubuntu-22.04 -- bash -lc \"source "
            "~/miniconda3/etc/profile.d/conda.sh && conda activate base "
            "&& cd /mnt/e/oxpicious-trading && python -m builds.index\"\n",
            flush=True,
        )
        sys.exit(1)
    return n


async def _backfill_dates_map_if_stale(conn) -> None:
    """One-time guard: map empty but main table has PAIR rows (pre-map
    schema migration) → populate from the pair grain's DISTINCT dates.

    The map tracks PAIR-grain (sec_type='index') dates ONLY — it drives
    the pair missing-date detection and the corr grid selection. Industry
    rows never feed it (industry grain self-heals via its own probe in
    step 4), so a date with industry rows but no closes yet can never be
    masked from pair detection.
    """
    n_map = await conn.fetchval(f"SELECT count(*) FROM {DATES_MAP_TABLE}")
    if n_map:
        return
    n_main = await conn.fetchval(
        f"SELECT count(*) FROM {TABLE} WHERE sec_type = 'index'"
    )
    if not n_main:
        return
    print(f"    -> dates map empty but {TABLE} has {n_main:,} PAIR rows; "
          f"backfilling map (one-time)...", flush=True)
    await conn.execute(
        f"INSERT INTO {DATES_MAP_TABLE} (date) "
        f"SELECT DISTINCT date FROM {TABLE} WHERE sec_type = 'index' "
        f"ON CONFLICT (date) DO NOTHING"
    )


async def _fetch_map_dates(conn) -> Set[datetime.date]:
    rows = await conn.fetch(f"SELECT date FROM {DATES_MAP_TABLE}")
    return {r["date"] for r in rows}


async def _register_dates(conn, dates: Set[datetime.date]) -> None:
    """Record written dates in the map (idempotent)."""
    if not dates:
        return
    rows = [(d,) for d in sorted(dates)]
    await conn.executemany(
        f"INSERT INTO {DATES_MAP_TABLE} (date) VALUES ($1) "
        f"ON CONFLICT (date) DO NOTHING",
        rows,
    )


async def _industry_catchup_dates(conn) -> Set[datetime.date]:
    """Map dates where PAIR rows exist but INDUSTRY rows do not.

    Probes the dates map (~1.6K rows) against the (sec_type, date)
    secondary index — cheap. Keeps the industry grain self-healing after
    first-run migrations without rescanning the main table.
    """
    rows = await conn.fetch(f"""
        SELECT d.date
        FROM {DATES_MAP_TABLE} d
        WHERE NOT EXISTS (
            SELECT 1 FROM {TABLE} cs
            WHERE cs.sec_type = 'industry' AND cs.date = d.date
        )
    """)
    return {r["date"] for r in rows}


async def _dates_with_industry_rows(
    conn, dates: Set[datetime.date]
) -> Set[datetime.date]:
    """Which of ``dates`` already have INDUSTRY rows (indexed probe).

    The industry INSERT is a plain INSERT (no ON CONFLICT) — pruning its
    date list to dates genuinely missing industry rows keeps it
    conflict-free AND self-healing (a date that failed before its pair
    rows landed gets retried once its data exists).
    """
    if not dates:
        return set()
    rows = await conn.fetch(
        f"SELECT DISTINCT date FROM {TABLE} "
        f"WHERE sec_type = 'industry' AND date = ANY($1::date[])",
        sorted(dates),
    )
    return {r["date"] for r in rows}


# ---------------------------------------------------------------------------
#  Main pipeline
# ---------------------------------------------------------------------------
async def run_cross_stats(conn, *, force: bool = False) -> None:
    """Run the cross_stats pipeline on a caller-supplied connection.

    Args:
      conn: asyncpg connection (caller owns lifecycle).
      force: truncate + full recompute when True; missing dates only
        otherwise (early-return when up to date).
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  CROSS STATS (pair + industry grain) — builds.cross_stats",
          flush=True)
    print("=" * 78, flush=True)
    print_declared_blockers()
    if force:
        print("    mode: FORCE (full recompute)", flush=True)

    # ---- Step 0: composition preflight gate ---------------------------
    print("\n[0/5] Composition preflight gate...", flush=True)
    n_holdings = await check_composition_present(conn)
    print(f"    -> stats.sec_composition holds {n_holdings:,} index "
          f"holding rows — OK", flush=True)

    # ---- Step 1: determine target dates -------------------------------
    if force:
        print(f"\n[1/5] Force mode: truncating {TABLE}...", flush=True)
        await truncate_table_async(conn, TABLE)
        await truncate_table_async(conn, DATES_MAP_TABLE)
        await conn.execute(
            f"DROP INDEX IF EXISTS stats.{SEC_TYPE_DATE_INDEX}"
        )
        target_dates: Optional[Set[datetime.date]] = None
        print("    -> truncated (+ dates map); dropped secondary index; "
              "will recompute all rows", flush=True)
    else:
        print("\n[1/5] Detecting missing dates "
              "(source: index_identity vs dates map)...", flush=True)
        await _backfill_dates_map_if_stale(conn)
        source_rows = await conn.fetch(
            "SELECT DISTINCT date FROM stats.index_identity ORDER BY date"
        )
        source_dates = {r["date"] for r in source_rows}
        existing = await _fetch_map_dates(conn)
        target_dates = source_dates - existing
        print(f"    -> {len(target_dates)} dates missing from {TABLE} "
              f"(map has {len(existing)} of {len(source_dates)} source "
              f"dates)", flush=True)
        if not target_dates:
            # No-op run: the summary rollup still needs a refresh when the
            # last data-writing run predates it (or the summary table is
            # new/empty). The staleness probe touches only the tiny dates
            # map + summary PK, so steady-state no-op runs stay cheap.
            if await summary_is_stale(conn):
                print("    -> code summary stale; refreshing it now "
                      "(one-time after the last data write)...", flush=True)
                await refresh_code_summary(conn)
            print("    -> cross_stats up to date; nothing to do.",
                  flush=True)
            print(f"\n  cross_stats wall time: {time.time() - t0:.1f}s",
                  flush=True)
            return

    # ---- Compute lookback start for incremental mode -----------------
    # Max corr window + MA5 buffer — the pair grain's heaviest rolling
    # need. (Industry-grain trading amounts have NO rolling window.)
    _MA5_BUF: int = 5
    _LOOKBACK: int = max(CORR_WINDOWS) + _MA5_BUF  # 260 trading days
    start_date: Optional[datetime.date] = None
    if target_dates:
        min_target: datetime.date = min(target_dates)
        start_date = recent_trading_day_cutoff(_LOOKBACK, ref=min_target)
        print(f"    -> lookback window: {start_date} to {min_target} "
              f"({_LOOKBACK} trading days)", flush=True)

    # ---- Step 2: PAIR grain -------------------------------------------
    print("\n[2/5] PAIR grain: fetching index closes (benchmarks)...",
          flush=True)
    with timed("fetch"):
        index_closes = await fetch_index_closes(conn, start_date=start_date)
        n_indices = (index_closes["benchmark_code"].nunique()
                     if not index_closes.empty else 0)
        print(f"    -> {len(index_closes):,} index rows across "
              f"{n_indices} indices", flush=True)
        if index_closes.empty:
            print("    -> no index data; exiting.", flush=True)
            return

        # Recent-data pre-filter: drop delisted/suspended indices (no
        # identity rows in the last RECENT_TRADING_DAYS) — covers BOTH
        # the benchmark universe and subjects.
        cutoff = recent_trading_day_cutoff(RECENT_TRADING_DAYS)
        active_index_codes = await fetch_codes_with_recent_data_async(
            conn, "stats.index_identity", n_trading_days=RECENT_TRADING_DAYS,
        )
        before = int(index_closes["benchmark_code"].nunique())
        index_closes = index_closes[
            index_closes["benchmark_code"].isin(active_index_codes)
        ]
        after = int(index_closes["benchmark_code"].nunique())
        print(f"    -> recent-data pre-filter (cutoff={cutoff.isoformat()}, "
              f"{RECENT_TRADING_DAYS} trading days): kept {after} of "
              f"{before} indices", flush=True)
        if index_closes.empty:
            print("    -> no indices with recent data; exiting.",
                  flush=True)
            return

        print("    -> fetching composition shared weights (ALL pairs) "
              "+ codes-with-composition filter set...", flush=True)
        shared_weights = await fetch_shared_weights(conn)
        print(f"    -> {len(shared_weights):,} (subject, benchmark) "
              f"pairs with shared weights", flush=True)
        codes_with_comp = await fetch_codes_with_composition(conn)
        print(f"    -> {len(codes_with_comp):,} codes have composition "
              f"data (used to filter subjects)", flush=True)

        print("    -> fetching total_etf_trading_amount from "
              "stats.index_exts per (date, tracking_index)...", flush=True)
        etf_amount_by_index = await fetch_etf_amount_by_index(
            conn, start_date=start_date
        )
        if not etf_amount_by_index.empty:
            print(f"    -> {len(etf_amount_by_index):,} rows across "
                  f"{etf_amount_by_index['index_code'].nunique()} indices "
                  f"with tracking ETFs", flush=True)
        else:
            print("    -> no ETF->index mapping data; ETF amount columns "
                  "will be NULL.", flush=True)

        index_subject_closes = await fetch_index_subject_closes(
            conn, start_date=start_date
        )
        # Broad-market indices join the subject pool (they appear as
        # subjects in the Attribution view alongside all others).
        broad_subjects = index_closes[
            ~index_closes["benchmark_code"].isin(index_subject_closes["code"])
        ].rename(columns={
            "benchmark_code": "code",
            "benchmark_close": "subject_close",
        })
        if not broad_subjects.empty:
            index_subject_closes = pd.concat(
                [index_subject_closes, broad_subjects], ignore_index=True
            )
        before_idx = int(index_subject_closes["code"].nunique())
        index_subject_closes = index_subject_closes[
            index_subject_closes["code"].isin(active_index_codes)
        ]
        after_idx = int(index_subject_closes["code"].nunique())
        print(f"    -> {after_idx} of {before_idx} subjects "
              f"(recent-data filter + broad-include)", flush=True)

    if index_subject_closes.empty:
        print("    -> no subjects; skipping pair grain.", flush=True)
    else:
        print("\n[3/5] PAIR grain: building + COPY "
              f"({TABLE}, sec_type='index')...", flush=True)
        n = await build_and_insert(
            conn, index_subject_closes, index_closes, shared_weights,
            etf_amount_by_index, sec_type="index",
            target_dates=target_dates,
        )
        print(f"    -> pair grain total: {n:,} rows", flush=True)

    # ---- Step 4: INDUSTRY grain ---------------------------------------
    print(f"\n[4/5] INDUSTRY grain: INSERT...SELECT "
          f"(sec_type='industry')...", flush=True)
    await conn.execute(SET_WORK_MEM_SQL)
    if target_dates is None:
        sql, params = INDUSTRY_INSERT_SQL_FULL, []
        scope = "FULL (unbounded history, all pairs)"
    else:
        candidates = set(target_dates) | await _industry_catchup_dates(conn)
        have = await _dates_with_industry_rows(conn, candidates)
        industry_dates = candidates - have
        if industry_dates:
            sql = INDUSTRY_INSERT_SQL_INCREMENTAL
            params = [sorted(industry_dates)]
            scope = (f"INCREMENTAL ({len(industry_dates)} of "
                     f"{len(candidates)} candidate dates have no industry "
                     f"rows yet)")
        else:
            sql, params = None, []
            scope = (f"SKIP (all {len(candidates)} candidate dates already "
                     f"have industry rows)")
    print(f"    -> {scope}", flush=True)
    if sql is not None:
        with timed("industry-grain"):
            status = await conn.execute(sql, *params)
        print(f"    -> industry grain: {status}", flush=True)

    # ---- Step 5: register dates + post-create index -------------------
    # Post-create the secondary index FIRST so the written-dates probe
    # below is index-driven (in force mode the index was dropped for the
    # bulk load; incremental runs keep it live).
    print(f"\n[5/5] Registering written dates in {DATES_MAP_TABLE} + "
          f"post-creating secondary index...", flush=True)
    t_idx = time.time()
    await conn.execute(SEC_TYPE_DATE_INDEX_SQL)
    print(f"    -> index {SEC_TYPE_DATE_INDEX} ready "
          f"({time.time() - t_idx:.1f}s)", flush=True)
    # The map tracks PAIR-grain dates ONLY: register the dates that
    # ACTUALLY have pair rows (exact probe via the just-created index).
    # Registering planned target dates instead would mask dates whose
    # close data has not landed yet (index_identity races the EOD CSV
    # publish) — they would never be retried.
    rows_w = await conn.fetch(
        f"SELECT DISTINCT date FROM {TABLE} WHERE sec_type = 'index'"
    )
    written: Set[datetime.date] = {r["date"] for r in rows_w}
    await _register_dates(conn, written)
    msg = f"    -> map now covers {len(written)} pair-grain written dates"
    if target_dates is not None:
        masked = target_dates - written
        if masked:
            msg += (f"; {len(masked)} target dates have NO pair rows "
                    f"(closes not downloaded yet) — left unregistered: "
                    f"{sorted(masked)}")
    print(msg, flush=True)

    # ---- Step 5b: refresh the API-facing code summary -----------------
    # Data was written → membership/dates changed → always refresh. The
    # API (perf-attr codes/themes, intraday benchmark dropdown) reads
    # stats.cross_stats_code_summary instead of re-aggregating the 70M+
    # row main table per request.
    print(f"\n[5/5b] Refreshing {SUMMARY_TABLE}...", flush=True)
    await refresh_code_summary(conn)

    print(f"\n  cross_stats wall time: {time.time() - t0:.1f}s", flush=True)


# ---------------------------------------------------------------------------
#  Corr-only build (--corr sub-command)
# ---------------------------------------------------------------------------
async def run_corr_update(conn) -> None:
    """Recompute corr_20d/60d/255d for grid dates and upsert onto
    EXISTING rows (base columns untouched — see compute/_orchestrator).

    The main pipeline runs with corr OFF (the GPU tensor pass is the
    most expensive step and corr only changes on the stride-20 grid);
    this refreshes it independently. The earliest grid date reaches far
    back, so the lookback effectively keeps the full history — corr
    windows need it.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  CROSS STATS — CORR BUILD (stride grid only)", flush=True)
    print("=" * 78, flush=True)

    await check_composition_present(conn)
    await _backfill_dates_map_if_stale(conn)

    grid_dates = await fetch_corr_grid_dates(conn)
    grid_set: Set[datetime.date] = set(pd.to_datetime(grid_dates).date)
    present = await _fetch_map_dates(conn)
    target_dates = grid_set & present
    print(f"    -> {len(target_dates)} grid dates present in {TABLE}",
          flush=True)
    if not target_dates:
        print("    -> no grid dates to update; run the main pipeline "
              "first.", flush=True)
        return

    index_closes = await fetch_index_closes(conn)
    if index_closes.empty:
        print("    -> no index data; exiting.", flush=True)
        return
    shared_weights = await fetch_shared_weights(conn)
    etf_amount_by_index = await fetch_etf_amount_by_index(conn)
    index_subject_closes = await fetch_index_subject_closes(conn)

    # Same recent-data pre-filter as the main run — identical universes
    # so corr mode never INSERTs rows the main pipeline skipped.
    active_index_codes = await fetch_codes_with_recent_data_async(
        conn, "stats.index_identity", n_trading_days=RECENT_TRADING_DAYS,
    )
    index_closes = index_closes[
        index_closes["benchmark_code"].isin(active_index_codes)
    ]
    if index_closes.empty:
        print("    -> no indices with recent data; exiting.", flush=True)
        return
    broad_subjects = index_closes[
        ~index_closes["benchmark_code"].isin(index_subject_closes["code"])
    ].rename(columns={
        "benchmark_code": "code",
        "benchmark_close": "subject_close",
    })
    if not broad_subjects.empty:
        index_subject_closes = pd.concat(
            [index_subject_closes, broad_subjects], ignore_index=True
        )
    index_subject_closes = index_subject_closes[
        index_subject_closes["code"].isin(active_index_codes)
    ]
    if index_subject_closes.empty:
        print("    -> no subjects; exiting.", flush=True)
        return

    n = await build_and_insert(
        conn, index_subject_closes, index_closes, shared_weights,
        etf_amount_by_index, sec_type="index",
        target_dates=target_dates, with_corr=True,
    )
    print(f"    -> corr build total: {n:,} rows", flush=True)
    print(f"\n  corr build wall time: {time.time() - t0:.1f}s", flush=True)
