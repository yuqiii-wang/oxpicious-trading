"""Code-summary rollup maintenance for builds.cross_stats.

``stats.cross_stats_code_summary`` is a tiny per-(sec_type, code) rollup of
the main table (first/last date, n_dates, DISTINCT benchmarks array). The
API reads it instead of re-running a GROUP BY over the 70M+ row
hash-partitioned main table per request (~30s live vs milliseconds).

``stats.cross_stats_benchmark_weight_summary`` is the companion
per-(sec_type, benchmark_code, code) rollup of the LATEST
code_sec_shared_weight — the sec-alloc-live attribution cs_weight read,
which was a ~28s DISTINCT ON scan of the main table per request (no index
leads with benchmark_code; code is the HASH key, so every request
seq-scanned all 8 partitions). See
database/sql/stats/21_cross_stats_benchmark_weight_summary.sql.

Refresh policy (see the two DDL files):
  - ALWAYS after a data-writing run (pair/industry grain INSERT) — the
    write already changed membership/dates, and the ~30s one-scan refresh
    is amortized inside a multi-minute build.
  - On the incremental "up to date; nothing to do" early-return ONLY when
    STALE (summary MAX(last_date) < dates-map MAX(date), or the summary is
    empty while pair rows exist) — the staleness probe touches the tiny
    map + PK, so no-op runs stay cheap. The weight rollup adds one more
    stale state: the pair grain gone (force truncate without a rewrite).
  - ``--corr`` never changes code membership, dates, or weights → no
    refresh.

Full DELETE + recompute inside one transaction so removed subjects/
benchmarks/pairs (force-mode recompute, composition edits) never linger.
The re-INSERT is ON CONFLICT DO UPDATE-armored: the aggregate scans the
70M+ row main table for minutes while the table sits DELETEd-but-
uncommitted — a concurrent summary writer committing a key in that window
would otherwise kill the whole run with UniqueViolationError (observed
2026-09-12); the upsert absorbs it while the DELETE still purges stale
keys.
"""
from __future__ import annotations

from _common.build_commons import truncate_table_async

from builds.cross_stats._perf import timed
from builds.cross_stats.config import (
    DATES_MAP_TABLE,
    SET_WORK_MEM_SQL,
    SUMMARY_TABLE,
    TABLE,
    WEIGHT_SUMMARY_TABLE,
)

import logging
logger = logging.getLogger(__name__)

# Grains hosted in cross_stats — the summary mirrors ALL of them ('etf' is
# reserved/empty today; DELETE+aggregate simply yields no rows for it).
_SUMMARY_SEC_TYPES = ("index", "industry", "etf")

_REFRESH_SQL = f"""
    INSERT INTO {SUMMARY_TABLE}
        (sec_type, code, first_date, last_date, n_dates, benchmarks)
    SELECT
        sec_type,
        code,
        MIN(date)                                            AS first_date,
        MAX(date)                                            AS last_date,
        COUNT(DISTINCT date)::int                            AS n_dates,
        ARRAY_AGG(DISTINCT benchmark_code ORDER BY benchmark_code)
                                                             AS benchmarks
    FROM {TABLE}
    WHERE sec_type = ANY($1::text[])
    GROUP BY sec_type, code
    ON CONFLICT (sec_type, code) DO UPDATE SET
        first_date = EXCLUDED.first_date,
        last_date  = EXCLUDED.last_date,
        n_dates    = EXCLUDED.n_dates,
        benchmarks = EXCLUDED.benchmarks
"""

# Latest row per pair — code_sec_shared_weight is composition-snapshot-
# constant across a pair's dates, so this reproduces the API's former
# DISTINCT ON (code) ... ORDER BY code, date DESC exactly.
_WEIGHT_REFRESH_SQL = f"""
    INSERT INTO {WEIGHT_SUMMARY_TABLE}
        (sec_type, benchmark_code, code, last_date, code_sec_shared_weight)
    SELECT DISTINCT ON (sec_type, benchmark_code, code)
        sec_type, benchmark_code, code, date, code_sec_shared_weight
    FROM {TABLE}
    WHERE sec_type = ANY($1::text[])
    ORDER BY sec_type, benchmark_code, code, date DESC
    ON CONFLICT (sec_type, benchmark_code, code) DO UPDATE SET
        last_date = EXCLUDED.last_date,
        code_sec_shared_weight = EXCLUDED.code_sec_shared_weight
"""


async def summary_is_stale(conn) -> bool:
    """True when the summary needs a refresh on the no-op path.

    Stale = no summary rows for the pair grain while the main table has
    pair rows, or the summary's latest covered date lags the dates map
    (a newly written date is never registered before its pair rows land,
    so map MAX(date) == latest pair-grain date — see runner step 5).

    All probes are cheap: the summary check is a PK-grain count, the
    main-table existence probe short-circuits via the (sec_type, date)
    index, and the map max touches the ~1.7K-row map. A count(*) over
    the 70M+ row main table would defeat the no-op fast path.
    """
    n_summary = await conn.fetchval(
        f"SELECT count(*) FROM {SUMMARY_TABLE} WHERE sec_type = 'index'"
    )
    if n_summary:
        summary_max = await conn.fetchval(
            f"SELECT MAX(last_date) FROM {SUMMARY_TABLE} "
            f"WHERE sec_type = 'index'"
        )
        map_max = await conn.fetchval(f"SELECT MAX(date) FROM {DATES_MAP_TABLE}")
        return bool(
            summary_max is None
            or (map_max is not None and summary_max < map_max)
        )
    # Summary empty: stale only when the pair grain actually has rows.
    has_pair_rows = await conn.fetchval(
        f"SELECT 1 FROM {TABLE} WHERE sec_type = 'index' LIMIT 1"
    )
    return bool(has_pair_rows)


async def refresh_code_summary(conn) -> None:
    """Fully rebuild the summary rows for all hosted grains (one scan)."""
    with timed("code-summary"):
        # The aggregate scans the full pair grain (70M+ rows) — the same
        # session tuning as the industry INSERT...SELECT.
        await conn.execute(SET_WORK_MEM_SQL)
        async with conn.transaction():
            await conn.execute(
                f"DELETE FROM {SUMMARY_TABLE} WHERE sec_type = ANY($1::text[])",
                list(_SUMMARY_SEC_TYPES),
            )
            await conn.execute(_REFRESH_SQL, list(_SUMMARY_SEC_TYPES))
    n = await conn.fetchval(f"SELECT count(*) FROM {SUMMARY_TABLE}")
    logger.info(f"    -> code summary refreshed: {n:,} rows in {SUMMARY_TABLE}")


async def benchmark_weight_summary_is_stale(conn) -> bool:
    """True when the benchmark-weight rollup needs a refresh on the no-op
    path.

    Same cheap probes as summary_is_stale (tiny rollup PK + dates map,
    plus one indexed pair-grain existence probe):
      - rollup empty + pair grain has rows → stale (first deployment).
      - pair grain EMPTY while the rollup holds rows → stale (a force
        truncate without a rewrite — the refresh re-empties it).
      - rollup MAX(last_date) < map MAX(date) → a new date was written
        (never registered before its pair rows land).
    """
    n_rollup = await conn.fetchval(
        f"SELECT count(*) FROM {WEIGHT_SUMMARY_TABLE} "
        f"WHERE sec_type = 'index'"
    )
    has_pair_rows = await conn.fetchval(
        f"SELECT 1 FROM {TABLE} WHERE sec_type = 'index' LIMIT 1"
    )
    if n_rollup:
        if not has_pair_rows:
            return True
        rollup_max = await conn.fetchval(
            f"SELECT MAX(last_date) FROM {WEIGHT_SUMMARY_TABLE} "
            f"WHERE sec_type = 'index'"
        )
        map_max = await conn.fetchval(f"SELECT MAX(date) FROM {DATES_MAP_TABLE}")
        return bool(
            rollup_max is None
            or (map_max is not None and rollup_max < map_max)
        )
    return bool(has_pair_rows)


async def refresh_benchmark_weight_summary(conn) -> None:
    """Fully rebuild the latest-weight rollup for all hosted grains (one
    scan — same transaction shape as refresh_code_summary)."""
    with timed("benchmark-weight-summary"):
        await conn.execute(SET_WORK_MEM_SQL)
        async with conn.transaction():
            await conn.execute(
                f"DELETE FROM {WEIGHT_SUMMARY_TABLE} "
                f"WHERE sec_type = ANY($1::text[])",
                list(_SUMMARY_SEC_TYPES),
            )
            await conn.execute(_WEIGHT_REFRESH_SQL, list(_SUMMARY_SEC_TYPES))
    n = await conn.fetchval(f"SELECT count(*) FROM {WEIGHT_SUMMARY_TABLE}")
    logger.info(f"    -> benchmark weight summary refreshed: {n:,} rows in "
                f"{WEIGHT_SUMMARY_TABLE}")
