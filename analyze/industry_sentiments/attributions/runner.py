"""Runner for the industry-attributions step (run_attributions).

Orchestrates the write phase: guard/preview probes, incremental
target-date pruning, phased truncate/index DDL + INSERTs (merged
broad-market, member-index two-phase, equal variant) with replica-lag
throttling between phases, analysis-identity upserts, and the sanity
summary.
"""
from __future__ import annotations

import datetime
import time
from typing import List, Optional, Set

from _common.build_commons import (
    truncate_table_async,
)
from _common.db_commons import (
    DEFAULT_MAX_REPLICA_LAG_MB,
    _wait_for_replica_lag_async,
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

import logging
logger = logging.getLogger(__name__)

# Date-batch sizes for the phased INSERT..SELECT writes (rows/commit
# bounding — see _date_batches; tuned to the observed per-date densities:
# ~7K rows/date broad-market, ~7K member, ~14K equal at the current
# industry/benchmark counts).
_BROAD_DATE_BATCH = 20
_MEMBER_DATE_BATCH = 20
_EQUAL_DATE_BATCH = 10


async def run_attributions(
    conn,
    *,
    target_dates: Optional[Set[datetime.date]] = None,
    force: bool = False,
    max_replica_lag_mb: float | None = DEFAULT_MAX_REPLICA_LAG_MB,
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

    PHASED, not one mega-transaction: the write phase commits per phase
    (truncate+index DDL, broad-market INSERT, member INSERTs, equal
    INSERT, index rebuild) with a replica-lag throttle pause between
    phases. The old single transaction kept one snapshot alive for
    minutes and gave the standby no catch-up point — in the bulk-load
    regime the primary out-generated the replica's replay and the slot
    retained tens of GB of WAL. Trade-off: an interrupted force run now
    leaves the table partially rebuilt instead of rolling back — rerun
    the --force rebuild and it truncates + rebuilds from scratch.
    ``work_mem`` / ``maintenance_work_mem`` bumps are session-level
    (a plain SET inside a transaction would revert at its commit).

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
    logger.info("\n" + "=" * 78)
    logger.info("  INDUSTRY ATTRIBUTIONS (internal step of industry_sentiments)")
    logger.info("=" * 78)

    incremental = (not force
                   and target_dates is not None
                   and len(target_dates) > 0)
    if force:
        logger.info("    mode: FORCE (full recompute)")
    elif incremental:
        logger.info(f"    mode: incremental ({len(target_dates)} target dates)")

    # ---- Step 1: guard — check upstream + member-index availability --
    # The broad-market INSERT needs the stats.cross_stats INDUSTRY grain
    # (built by builds.cross_stats from the pair rows); the
    # member-index INSERT only needs sec_composition. Only exit if BOTH
    # are empty (nothing to materialize at all).
    n_src = await conn.fetchval(COUNT_SOURCE_SQL)
    n_members = await conn.fetchval(COUNT_MEMBER_INDICES_SQL)
    if not n_src and not n_members:
        logger.info("\n[a1/6] stats.cross_stats has no industry rows AND "
              "no member indices with composition data — nothing to "
              "materialize. Skipping attributions step.")
        return
    logger.info(f"\n[a1/6] Source stats.cross_stats (sec_type='industry'): "
          f"{n_src:,} industry-grain rows | {n_members} non-broad member "
          f"indices with composition data.")

    # ---- Step 2: preview dimensions ----------------------------------
    logger.info("\n[a2/6] Previewing output dimensions...")
    dims = await conn.fetchrow(PREVIEW_DIMENSIONS_SQL)
    n_industries = dims["n_industries"] if dims else 0
    n_benchmarks = dims["n_benchmarks"] if dims else 0
    logger.info(f"      broad-market: {n_industries} industries x {n_benchmarks} "
          f"benchmarks (max {n_industries * n_benchmarks:,} pairs, "
          f"materialized per date where member indices have data)")
    mdims = await conn.fetchrow(PREVIEW_MEMBER_DIMENSIONS_SQL)
    n_md_ind = mdims["n_industries"] if mdims else 0
    n_md_mem = mdims["n_member_indices"] if mdims else 0
    logger.info(f"      member-index: {n_md_ind} industries x {n_md_mem} "
          f"non-broad member indices")

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
            logger.info(f"    -> pruned {dropped} target date(s) already covered "
                  f"by {TABLE}")
        if not sorted_dates:
            logger.info("    -> all target dates already present — nothing to do.")
            logger.info(f"\n  attributions wall time: "
                  f"{time.time() - t0:.1f}s")
            return
        logger.info(f"    -> {len(sorted_dates)} target date(s) to materialize "
              f"({sorted_dates[0]} .. {sorted_dates[-1]})")

    # ---- Steps 4-6b: phased writes, throttle between phases -----------
    # Each phase is its own committed statement (asyncpg autocommit), and
    # every phase boundary pauses while the streaming standby's replay
    # lag exceeds ``max_replica_lag_mb`` — the slot's WAL bound advances
    # between phases instead of a minutes-long transaction pinning it.
    # Session-level work_mem bumps apply to every phase; a plain SET
    # inside a transaction would revert at its commit.
    async def _throttle() -> None:
        if max_replica_lag_mb is not None:
            await _wait_for_replica_lag_async(conn, max_replica_lag_mb)

    def _date_batches(dates: List[datetime.date], size: int):
        """Split the date axis into ~size-date batches — each batch is
        ONE INSERT..SELECT statement / one COMMIT, so a full-history
        force rebuild commits ~100K-row chunks instead of retaining
        ~18M rows of WAL behind one statement (the bulk-write rule)."""
        for i in range(0, len(dates), size):
            yield dates[i:i + size]

    # The phases run as date-batched INSERT..SELECT statements (the
    # INCREMENTAL SQL variants, date+lookback parameterized) in BOTH
    # modes — force mode's axis is the source grain's own dates (the
    # FULL all-at-once variants are retired from the runner: one
    # statement per phase retained ~18M rows of WAL behind one commit).
    if incremental:
        phase_dates: List[datetime.date] = sorted_dates
    else:
        phase_dates = [
            r["date"] for r in await conn.fetch(
                "SELECT DISTINCT date FROM stats.cross_stats "
                "WHERE sec_type = 'industry' ORDER BY date")
        ]
        logger.info(f"    -> force-mode date axis: {len(phase_dates)} "
              f"source dates")

    if not incremental:
        logger.info(f"\n[a3/6] Truncating {TABLE} + {MAP_TABLE} "
              f"(full recompute)...")
        async with conn.transaction():
            await truncate_table_async(conn, TABLE)
            await truncate_table_async(conn, MAP_TABLE)
            logger.info(f"      Dropping {len(_SECONDARY_INDEXES)} secondary "
                  f"index(es) (force-mode optimization, PK kept)...")
            for idx_name in _SECONDARY_INDEXES:
                await conn.execute(f"DROP INDEX IF EXISTS analysis.{idx_name}")
        await conn.execute(SET_MAINTENANCE_WORK_MEM_SQL)
    else:
        logger.info(f"\n[a3/6] Incremental mode — no truncate (dates are "
              f"pruned to absent ones; plain INSERT).")
    await conn.execute(SET_WORK_MEM_SQL)

    # ---- Steps 4+5: MERGED broad-market INSERT -------------------
    # A single INSERT...SELECT computes ALL columns (weights +
    # non_this_industry_*) in one CTE pass — no separate UPDATE, no
    # per-industry loop. Incremental variant adds a date filter, a
    # trading-day-precise lookback cap ($2), needed-pairs pruning,
    # and the liquidity split out of the heavy warm-up path.
    n_total_broad = 0
    if not n_src:
        logger.info("\n[a4-5/6] SKIPPED (no broad-market source data).")
    elif phase_dates:
        t_broad = time.time()
        logger.info(f"\n[a4-5/6] MERGED broad-market INSERT "
              f"({len(phase_dates)} dates in batches of "
              f"{_BROAD_DATE_BATCH}, lookback {LOOKBACK_TRADING_DAYS}td + "
              f"{LOOKBACK_EXTRA_CALENDAR_DAYS}d margin)...")
        for batch in _date_batches(phase_dates, _BROAD_DATE_BATCH):
            await _throttle()
            lookback_date = await fetch_incremental_lookback_date(
                conn, batch[0])
            status = await conn.execute(
                MERGED_BROAD_MARKET_INSERT_SQL_INCREMENTAL,
                batch, lookback_date,
            )
            n_total_broad += _parse_insert_count(status)
        logger.info(f"        -> {n_total_broad:,} rows inserted "
              f"({time.time() - t_broad:.1f}s)")

    # ---- Step 6: member-index (map populate + expansion, all-at-once) ----
    await _throttle()
    t_member = time.time()
    logger.info("\n[a6/6] Member-index INSERT (map populate + expansion, "
          "all-at-once)...")
    status_map = await conn.execute(MEMBER_INDEX_MAP_POPULATE_SQL)
    n_total_map = _parse_insert_count(status_map)
    n_total_member = 0
    for batch in _date_batches(phase_dates, _MEMBER_DATE_BATCH):
        await _throttle()
        status_mi = await conn.execute(
            MEMBER_INDEX_INSERT_SQL_INCREMENTAL, batch
        )
        n_total_member += _parse_insert_count(status_mi)
    logger.info(f"      -> map={n_total_map:,} rows, member={n_total_member:,} "
          f"rows ({time.time() - t_member:.1f}s)")

    # ---- Step 6b: equal-variant INSERT (all-at-once) --------------
    # Copies ALL trading_amt rows (broad-market + member-index) to
    # equal rows, dividing industry_shared_weight by N (active member
    # index count). benchmark_shared_weight and all
    # non_this_industry_* columns are copied unchanged.
    await _throttle()
    t_eq = time.time()
    logger.info(f"\n[a6b/6] Equal-variant INSERT "
          f"({len(phase_dates)} dates in batches of {_EQUAL_DATE_BATCH})...")
    n_eq = 0
    for batch in _date_batches(phase_dates, _EQUAL_DATE_BATCH):
        await _throttle()
        status_eq = await conn.execute(
            EQUAL_INSERT_SQL_INCREMENTAL, batch
        )
        n_eq += _parse_insert_count(status_eq)
    logger.info(f"      -> {n_eq:,} equal rows inserted "
          f"({time.time() - t_eq:.1f}s)")

    # ---- Recreate secondary index + ANALYZE (force mode only) ----
    # The index was DROPPED above so ALL INSERTs paid zero index
    # maintenance; rebuild it in one bulk build + refresh planner stats.
    if not incremental:
        await _throttle()
        t_idx = time.time()
        logger.info(f"\n      Recreating {len(_SECONDARY_INDEXES)} secondary "
              f"index(es) (bulk build, maintenance_work_mem=512MB)...")
        for idx_ddl in _CREATE_SECONDARY_INDEX_DDL:
            await conn.execute(idx_ddl)
        logger.info(f"      indexes rebuilt in {time.time() - t_idx:.1f}s")
        await conn.execute(ANALYZE_TABLE_SQL)
        logger.info(f"      ANALYZE {TABLE} done")

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
    logger.info("\n      Summary by (benchmark_code, attribution_type) "
          "[top 40, broad-market first]:")
    for r in summary:
        tag = "BROAD" if r["is_broad"] else "MEMBER"
        logger.info(f"        {r['benchmark_code']:8s} [{tag}] "
              f"{r['attribution_type']:11s}: "
              f"{r['n_rows']:>9,} rows . {r['n_industries']:>3} ind . "
              f"{r['first_date']} -> {r['last_date']} . "
              f"avg_isw={r['avg_isw']} avg_bsw={r['avg_bsw']} . "
              f"zero_overlap={r['n_zero_overlap']:,}")

    logger.info(f"\n  attributions wall time: {time.time() - t0:.1f}s")


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
