"""Runner for the industry-attributions step (run_attributions).

Orchestrates the write phase: guard/preview probes, incremental
target-date pruning, one transaction wrapping truncate/index DDL + all
INSERTs (merged broad-market, member-index two-phase, equal variant),
analysis-identity upserts, and the sanity summary.
"""
from __future__ import annotations

import datetime
import time
from typing import List, Optional, Set

from _common.build_commons import (
    truncate_table_async,
)
from analyze._common import upsert_analysis_identity
from analyze.industry_sentiments.attributions.config import (
    ANALYSIS_DESCRIPTION,
    ANALYSIS_NAME,
    ANALYZE_TABLE_SQL,
    LOOKBACK_EXTRA_CALENDAR_DAYS,
    LOOKBACK_TRADING_DAYS,
    MAP_TABLE,
    SET_MAINTENANCE_WORK_MEM_SQL,
    SET_WORK_MEM_SQL,
    TABLE,
    _CREATE_SECONDARY_INDEX_DDL,
    _SECONDARY_INDEXES,
)
from analyze.industry_sentiments.attributions.queries import (
    COUNT_MEMBER_INDICES_SQL,
    COUNT_SOURCE_SQL,
    PREVIEW_DIMENSIONS_SQL,
    PREVIEW_MEMBER_DIMENSIONS_SQL,
    fetch_incremental_lookback_date,
    find_missing_attribution_dates,
)
from analyze.industry_sentiments.attributions.sql_broad_market import (
    MERGED_BROAD_MARKET_INSERT_SQL_FULL,
    MERGED_BROAD_MARKET_INSERT_SQL_INCREMENTAL,
)
from analyze.industry_sentiments.attributions.sql_equal import (
    EQUAL_INSERT_SQL_FULL,
    EQUAL_INSERT_SQL_INCREMENTAL,
)
from analyze.industry_sentiments.attributions.sql_member_index import (
    MEMBER_INDEX_INSERT_SQL_FULL,
    MEMBER_INDEX_INSERT_SQL_INCREMENTAL,
    MEMBER_INDEX_MAP_POPULATE_SQL,
)


async def run_attributions(
    conn,
    *,
    target_dates: Optional[Set[datetime.date]] = None,
    force: bool = False,
) -> None:
    """Run the industry-attribution aggregation pipeline.

    Reuses the caller's DB connection (does not open/close its own) so the
    sentiments + correlations + attributions steps form a single
    atomic-ish batch.

    ALL-AT-ONCE (no per-industry loop): the broad-market weights AND the
    non_this_industry_* columns are computed by the single MERGED
    INSERT...SELECT (one CTE pass over the source), the member-index rows
    come from one map populate + one expansion INSERT, and the equal
    variant is one INSERT...SELECT. A ``work_mem`` bump keeps the hash
    aggregates in memory.

    TRANSACTIONAL: the whole write phase (truncate/index DDL in force
    mode + all INSERTs) runs inside one transaction, so an interrupted
    run rolls back completely instead of leaving a partially-populated
    table (the failure mode that previously required manual repair).

    PLAIN INSERT, NO ON CONFLICT: incremental target dates are pruned
    (find_missing_attribution_dates) to dates genuinely absent from the
    table before any write, so upserts are unnecessary. Already-covered
    dates (e.g. --with-corr window-end dates the table already has) are
    skipped — their stored values are deterministic functions of the same
    source data, so refreshing them would be a no-op anyway.

    INCREMENTAL LOOKBACK CAP (B-A5, implemented 2026-08-30): the merged
    INSERT's stock/benchmark history scans read only LOOKBACK_TRADING_DAYS
    (510) broad-benchmark trading days before the earliest target date
    (500 window rows + LAG row + grid margin, shifted back by
    LOOKBACK_EXTRA_CALENDAR_DAYS for per-stock LAG suspension slack)
    instead of full history since 2020. The chain is additionally pruned
    to the (industry, benchmark) pairs that actually occur at the target
    dates (needed_pairs) and the liquidity join is split out of the
    heavy warm-up path (trading_amount has no rolling window — it is
    computed at target dates only). Measured on the 1-date probe
    (temp_scripts/probe_attributions_parity.py, 2026-08-27): the capped
    INSERT runs in 57.3s vs 190.3s for the same SQL with a full-history
    lookback (3.3x), and bit-identical output (see SKILL.md §B-A5).

    FORCE-MODE INDEX OPTIMIZATION: in force mode the secondary index is
    DROPPED before the bulk INSERTs so they pay zero index maintenance,
    then RECREATED + ANALYZE inside the same transaction. The PK is kept
    throughout for dedup safety. Incremental mode keeps the index (daily
    inserts are small).

    Pipeline
      1. Guard: if BOTH the stats.cross_stats industry grain is empty AND
         there are no member indices with composition data, exit gracefully.
      2. Preview: report distinct industries x benchmarks + member indices.
      3. Incremental only: prune target dates to genuinely missing dates
         (skip + return when nothing remains).
      4. (one transaction) force: TRUNCATE both tables + DROP secondary
         index. Then the MERGED broad-market INSERT (full or
         date-filtered), the member-index map populate + expansion INSERT,
         and the equal-variant INSERT. Force: recreate index + ANALYZE.
      5. Upsert analysis.analysis_identity (name='industry_attributions'
         + name='industry_member_index_map').
      6. Sanity summary by (benchmark_code, attribution_type).

    Args:
      target_dates: when non-empty (and force=False), only rows whose
        date is in this set are inserted (incremental mode); dates
        already present in the table are pruned. When None/empty (and
        force=False) the step falls back to a full recompute.
      force: when True, truncate the tables first and recompute all rows.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  INDUSTRY ATTRIBUTIONS (internal step of industry_sentiments)",
          flush=True)
    print("=" * 78, flush=True)

    incremental = (not force
                   and target_dates is not None
                   and len(target_dates) > 0)
    if force:
        print("    mode: FORCE (full recompute)", flush=True)
    elif incremental:
        print(f"    mode: incremental ({len(target_dates)} target dates)",
              flush=True)

    # ---- Step 1: guard — check upstream + member-index availability --
    # The broad-market INSERT needs the stats.cross_stats INDUSTRY grain
    # (built by builds.cross_stats from the pair rows); the
    # member-index INSERT only needs sec_composition. Only exit if BOTH
    # are empty (nothing to materialize at all).
    n_src = await conn.fetchval(COUNT_SOURCE_SQL)
    n_members = await conn.fetchval(COUNT_MEMBER_INDICES_SQL)
    if not n_src and not n_members:
        print("\n[a1/6] stats.cross_stats has no industry rows AND "
              "no member indices with composition data — nothing to "
              "materialize. Skipping attributions step.", flush=True)
        return
    print(f"\n[a1/6] Source stats.cross_stats (sec_type='industry'): "
          f"{n_src:,} industry-grain rows | {n_members} non-broad member "
          f"indices with composition data.", flush=True)

    # ---- Step 2: preview dimensions ----------------------------------
    print("\n[a2/6] Previewing output dimensions...", flush=True)
    dims = await conn.fetchrow(PREVIEW_DIMENSIONS_SQL)
    n_industries = dims["n_industries"] if dims else 0
    n_benchmarks = dims["n_benchmarks"] if dims else 0
    print(f"      broad-market: {n_industries} industries x {n_benchmarks} "
          f"benchmarks (max {n_industries * n_benchmarks:,} pairs, "
          f"materialized per date where member indices have data)",
          flush=True)
    mdims = await conn.fetchrow(PREVIEW_MEMBER_DIMENSIONS_SQL)
    n_md_ind = mdims["n_industries"] if mdims else 0
    n_md_mem = mdims["n_member_indices"] if mdims else 0
    print(f"      member-index: {n_md_ind} industries x {n_md_mem} "
          f"non-broad member indices", flush=True)

    # ---- Step 3: prune incremental target dates ------------------------
    # Plain INSERT (no ON CONFLICT) is only safe for dates genuinely absent
    # from the table. Drop already-covered dates (e.g. --with-corr
    # window-end dates the table already has): their stored values are
    # deterministic functions of the same source data, so skipping is a
    # no-op refresh for free.
    sorted_dates: List[datetime.date] = []
    if incremental:
        missing = await find_missing_attribution_dates(conn)
        sorted_dates = sorted(target_dates & missing)
        dropped = len(target_dates) - len(sorted_dates)
        if dropped:
            print(f"    -> pruned {dropped} target date(s) already covered "
                  f"by {TABLE}", flush=True)
        if not sorted_dates:
            print("    -> all target dates already present — nothing to do.",
                  flush=True)
            print(f"\n  attributions wall time: "
                  f"{time.time() - t0:.1f}s", flush=True)
            return
        print(f"    -> {len(sorted_dates)} target date(s) to materialize "
              f"({sorted_dates[0]} .. {sorted_dates[-1]})", flush=True)

    # ---- Steps 4-6b: one transaction, all-at-once ---------------------
    # The whole write phase (truncate/index DDL + INSERTs) is atomic: an
    # interrupted run rolls back completely instead of leaving a partially
    # populated table (the historical failure mode that needed manual
    # repair). FORCE-MODE INDEX OPTIMIZATION: the secondary index is
    # dropped before the bulk INSERTs (zero index maintenance) and
    # recreated after, in the same transaction. PK is kept for dedup.
    async with conn.transaction():
        if not incremental:
            print(f"\n[a3/6] Truncating {TABLE} + {MAP_TABLE} "
                  f"(full recompute)...", flush=True)
            await truncate_table_async(conn, TABLE)
            await truncate_table_async(conn, MAP_TABLE)
            print(f"      Dropping {len(_SECONDARY_INDEXES)} secondary "
                  f"index(es) (force-mode optimization, PK kept)...",
                  flush=True)
            for idx_name in _SECONDARY_INDEXES:
                await conn.execute(f"DROP INDEX IF EXISTS analysis.{idx_name}")
            await conn.execute(SET_MAINTENANCE_WORK_MEM_SQL)
        else:
            print(f"\n[a3/6] Incremental mode — no truncate (dates are "
                  f"pruned to absent ones; plain INSERT).", flush=True)
        await conn.execute(SET_WORK_MEM_SQL)

        # ---- Steps 4+5: MERGED broad-market INSERT -------------------
        # A single INSERT...SELECT computes ALL columns (weights +
        # non_this_industry_*) in one CTE pass — no separate UPDATE, no
        # per-industry loop. Incremental variant adds a date filter, a
        # trading-day-precise lookback cap ($2), needed-pairs pruning,
        # and the liquidity split out of the heavy warm-up path.
        n_total_broad = 0
        if not n_src:
            print("\n[a4-5/6] SKIPPED (no broad-market source data).",
                  flush=True)
        elif incremental:
            lookback_date = await fetch_incremental_lookback_date(
                conn, sorted_dates[0])
            t_broad = time.time()
            print(f"\n[a4-5/6] MERGED broad-market INSERT (incremental, "
                  f"{len(sorted_dates)} dates, lookback "
                  f"{LOOKBACK_TRADING_DAYS}td + "
                  f"{LOOKBACK_EXTRA_CALENDAR_DAYS}d margin -> "
                  f"{lookback_date})...", flush=True)
            status = await conn.execute(
                MERGED_BROAD_MARKET_INSERT_SQL_INCREMENTAL,
                sorted_dates, lookback_date,
            )
            n_total_broad = _parse_insert_count(status)
            print(f"        -> {status} | {n_total_broad:,} rows inserted "
                  f"({time.time() - t_broad:.1f}s)", flush=True)
        else:
            t_broad = time.time()
            print(f"\n[a4-5/6] MERGED broad-market INSERT (all-at-once, "
                  f"indexes dropped, PK kept)...", flush=True)
            status = await conn.execute(MERGED_BROAD_MARKET_INSERT_SQL_FULL)
            n_total_broad = _parse_insert_count(status)
            print(f"        -> {status} | {n_total_broad:,} rows inserted "
                  f"({time.time() - t_broad:.1f}s)", flush=True)

        # ---- Step 6: member-index (map populate + expansion, all-at-once) ----
        t_member = time.time()
        print("\n[a6/6] Member-index INSERT (map populate + expansion, "
              "all-at-once)...", flush=True)
        status_map = await conn.execute(MEMBER_INDEX_MAP_POPULATE_SQL)
        n_total_map = _parse_insert_count(status_map)
        if incremental:
            status_mi = await conn.execute(
                MEMBER_INDEX_INSERT_SQL_INCREMENTAL, sorted_dates
            )
        else:
            status_mi = await conn.execute(MEMBER_INDEX_INSERT_SQL_FULL)
        n_total_member = _parse_insert_count(status_mi)
        print(f"      -> map={n_total_map:,} rows, member={n_total_member:,} "
              f"rows ({time.time() - t_member:.1f}s)", flush=True)

        # ---- Step 6b: equal-variant INSERT (all-at-once) --------------
        # Copies ALL trading_amt rows (broad-market + member-index) to
        # equal rows, dividing industry_shared_weight by N (active member
        # index count). benchmark_shared_weight and all
        # non_this_industry_* columns are copied unchanged.
        t_eq = time.time()
        if incremental:
            print(f"\n[a6b/6] Equal-variant INSERT (incremental, "
                  f"{len(sorted_dates)} target dates)...", flush=True)
            status_eq = await conn.execute(
                EQUAL_INSERT_SQL_INCREMENTAL, sorted_dates
            )
        else:
            print("\n[a6b/6] Equal-variant INSERT (full, copy from "
                  "trading_amt)...", flush=True)
            status_eq = await conn.execute(EQUAL_INSERT_SQL_FULL)
        n_eq = _parse_insert_count(status_eq)
        print(f"      -> {status_eq} | {n_eq:,} equal rows inserted "
              f"({time.time() - t_eq:.1f}s)", flush=True)

        # ---- Recreate secondary index + ANALYZE (force mode only) ----
        # The index was DROPPED above so ALL INSERTs paid zero index
        # maintenance; rebuild it in one bulk build + refresh planner
        # stats. Both are transactional, so they roll back with the rest.
        if not incremental:
            t_idx = time.time()
            print(f"\n      Recreating {len(_SECONDARY_INDEXES)} secondary "
                  f"index(es) (bulk build, maintenance_work_mem=512MB)...",
                  flush=True)
            for idx_ddl in _CREATE_SECONDARY_INDEX_DDL:
                await conn.execute(idx_ddl)
            print(f"      indexes rebuilt in {time.time() - t_idx:.1f}s",
                  flush=True)
            await conn.execute(ANALYZE_TABLE_SQL)
            print(f"      ANALYZE {TABLE} done", flush=True)

    # Upsert analysis_identity for the mapping table.
    await upsert_analysis_identity(
        conn,
        name="industry_member_index_map",
        detail_name="industry_member_index_map",
        description=(
            "Pre-computed mapping of each industry to its NON-BROAD "
            "member indices, with composition-derived shared weights "
            "frozen at the latest sec_composition snapshot. Used to "
            "fast-track analysis.industry_attributions population."
        ),
    )

    # ---- Step 7: upsert analysis_identity ----------------------------
    await upsert_analysis_identity(
        conn,
        name=ANALYSIS_NAME,
        detail_name=ANALYSIS_NAME,
        description=ANALYSIS_DESCRIPTION,
    )

    # ---- Step 8: sanity summary --------------------------------------
    summary = await conn.fetch("""
        SELECT ia.benchmark_code,
               ia.attribution_type,
               BOOL_OR(sit.is_broad_market) AS is_broad,
               COUNT(*) AS n_rows,
               COUNT(DISTINCT ia.industry_id) AS n_industries,
               MIN(ia.date) AS first_date,
               MAX(ia.date) AS last_date,
               ROUND(AVG(ia.industry_shared_weight), 4) AS avg_isw,
               ROUND(AVG(ia.benchmark_shared_weight), 4) AS avg_bsw,
               COUNT(*) FILTER (WHERE ia.industry_shared_weight = 0
                                 AND ia.benchmark_shared_weight = 0)
                   AS n_zero_overlap
        FROM analysis.industry_attributions ia
        LEFT JOIN stats.sec_index_tags sit ON sit.code = ia.benchmark_code
        GROUP BY ia.benchmark_code, ia.attribution_type
        ORDER BY is_broad DESC, ia.benchmark_code, ia.attribution_type
        LIMIT 40
    """)
    print("\n      Summary by (benchmark_code, attribution_type) "
          "[top 40, broad-market first]:", flush=True)
    for r in summary:
        tag = "BROAD" if r["is_broad"] else "MEMBER"
        print(f"        {r['benchmark_code']:8s} [{tag}] "
              f"{r['attribution_type']:11s}: "
              f"{r['n_rows']:>9,} rows . {r['n_industries']:>3} ind . "
              f"{r['first_date']} -> {r['last_date']} . "
              f"avg_isw={r['avg_isw']} avg_bsw={r['avg_bsw']} . "
              f"zero_overlap={r['n_zero_overlap']:,}", flush=True)

    print(f"\n  attributions wall time: {time.time() - t0:.1f}s", flush=True)


def _parse_insert_count(status: str) -> int:
    """Parse the row count from an asyncpg INSERT status string.

    asyncpg ``Connection.execute`` returns a status like
    ``"INSERT 0 18128883"``. The third token is the inserted row count.
    Returns 0 if the status can't be parsed.
    """
    if not status:
        return 0
    parts = status.split()
    if len(parts) >= 3 and parts[0] == "INSERT":
        try:
            return int(parts[2])
        except ValueError:
            return 0
    return 0
