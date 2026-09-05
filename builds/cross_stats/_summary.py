"""Code-summary rollup maintenance for builds.cross_stats.

``stats.cross_stats_code_summary`` is a tiny per-(sec_type, code) rollup of
the main table (first/last date, n_dates, DISTINCT benchmarks array). The
API reads it instead of re-running a GROUP BY over the 70M+ row
hash-partitioned main table per request (~30s live vs milliseconds).

Refresh policy (see database/sql/stats/15_cross_stats_code_summary.sql):
  - ALWAYS after a data-writing run (pair/industry grain INSERT) — the
    write already changed membership/dates, and the ~30s one-scan refresh
    is amortized inside a multi-minute build.
  - On the incremental "up to date; nothing to do" early-return ONLY when
    STALE (summary MAX(last_date) < dates-map MAX(date), or the summary is
    empty while pair rows exist) — the staleness probe touches the tiny
    map + PK, so no-op runs stay cheap.
  - ``--corr`` never changes code membership or dates → no refresh.

Full DELETE + recompute (not upsert) inside one transaction so removed
subjects/benchmarks (force-mode recompute, composition edits) never linger.
"""
from __future__ import annotations

from _common.build_commons import truncate_table_async

from builds.cross_stats._perf import timed
from builds.cross_stats.config import (
    DATES_MAP_TABLE,
    SET_WORK_MEM_SQL,
    SUMMARY_TABLE,
    TABLE,
)

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
    print(f"    -> code summary refreshed: {n:,} rows in {SUMMARY_TABLE}",
          flush=True)
