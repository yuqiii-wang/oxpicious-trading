"""external_resource_stats — per-day aggregation of the external request audit.

Rebuilds ``monitor.external_resource_stats`` (database/sql/monitoring/
06_external_resource_stats.sql) from ``monitor.external_resource_audit``:
one row per (service, status_code, date) with request / failure counts
and the avg / max whole-call duration_ms. The audit table is the source
of truth; this table is COMPUTED — never hand-written, never read as
fresh data.

Commands:

  python -m database.external_resource_stats
      Incremental refresh (the default): a DATE is stale — and is
      recomputed (its stats rows purged + re-aggregated, one statement,
      one commit) — when the audit gained rows after the date's last
      computed_at. A date's rows keep arriving until the GMT-midnight
      rollover (Beijing 08:00), so the newest dates recompute on every
      run; closed days converge to no-ops. Run it after download/LLM-heavy
      periods or from a scheduler.

  python -m database.external_resource_stats --force
      Purge the whole stats table and re-aggregate every audit date.

Pure SQL aggregation — no dataframes, so no cudf activation; the
per-date DELETE+INSERT is one chunk per partition-key date with the
0.1s between-chunk pause (the bulk-write rule, at the size where it
costs nothing). Connection from _common.db_commons (database/.env).

Reads connection parameters from database/.env via the shared
_common.db_commons helpers (never duplicate them).
"""
from __future__ import annotations

import argparse
import sys
import time

from psycopg import Connection

from _common.db_commons import get_db_connection

# The bulk-write rule's between-chunk cool-down — one chunk per date.
_CHUNK_PAUSE_S = 0.1

# Dates whose audit rows are newer than the date's last stats refresh
# (or that have no stats rows at all), oldest first.
_STALE_DATES_SQL = """
SELECT d.date, d.last_row_at, s.computed_at
FROM (
    SELECT date, max(created_at) AS last_row_at
    FROM monitor.external_resource_audit
    GROUP BY date
) d
LEFT JOIN (
    SELECT date, max(computed_at) AS computed_at
    FROM monitor.external_resource_stats
    GROUP BY date
) s USING (date)
WHERE s.computed_at IS NULL OR d.last_row_at > s.computed_at
ORDER BY d.date
"""

# One chunk = one date. Two statements run inside one explicit
# transaction (conn.transaction() works on the autocommit connection):
# a data-modifying CTE would NOT work here — DELETE and INSERT in one
# statement share a snapshot, so the re-inserted rows collide with the
# rows the same statement's DELETE has not yet made visible.
_PURGE_DATE_SQL = """
DELETE FROM monitor.external_resource_stats WHERE date = %s
"""

_AGGREGATE_DATE_SQL = """
INSERT INTO monitor.external_resource_stats
    (service, status_code, date, request_count, failed_count,
     avg_duration_ms, max_duration_ms, computed_at)
SELECT service, status_code, date,
       count(*),
       count(*) FILTER (WHERE is_failed),
       avg(duration_ms),
       max(duration_ms),
       now()
FROM monitor.external_resource_audit
WHERE date = %s
GROUP BY service, status_code, date
"""


def _fmt_ts(ts: object) -> str:
    return ts.isoformat(sep=" ", timespec="seconds") if ts else "-"  # type: ignore[union-attr]


def refresh(conn: Connection, *, force: bool = False) -> int:
    """Refresh the stats table; returns the number of dates rebuilt."""
    if force:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE monitor.external_resource_stats")
        print("[external_resource_stats] --force: purged the whole table")

    with conn.cursor() as cur:
        cur.execute(_STALE_DATES_SQL)
        stale = cur.fetchall()

    if not stale:
        print("[external_resource_stats] no stale dates — stats are current")
        return 0

    rebuilt = 0
    for date, last_row_at, computed_at in stale:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(_PURGE_DATE_SQL, (date,))
                cur.execute(_AGGREGATE_DATE_SQL, (date,))
                written = cur.rowcount
        rebuilt += 1
        print(
            f"[external_resource_stats] rebuilt {date}: {written} row(s) "
            f"(audit max {_fmt_ts(last_row_at)} > "
            f"stats {_fmt_ts(computed_at)})"
        )
        if rebuilt < len(stale):
            time.sleep(_CHUNK_PAUSE_S)

    # The incremental write is tiny, but a first build / --force rebuild
    # can cross the one-commit-chunk scale — keep planner stats fresh
    # (value-blind readers must not fall back to seq scans).
    with conn.cursor() as cur:
        cur.execute("ANALYZE monitor.external_resource_stats")

    print(f"[external_resource_stats] done — {rebuilt} date(s) rebuilt")
    return rebuilt


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m database.external_resource_stats",
        description="Rebuild monitor.external_resource_stats from the "
                    "external request audit (per service / status_code / "
                    "date).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="purge the whole stats table and re-aggregate every audit date",
    )
    args = parser.parse_args()

    # Heartbeat + run audit (monitor.*) — lazy so the module stays import-
    # light for tooling that only wants refresh().
    from _common.service_heartbeat import autoregister
    autoregister()

    conn = get_db_connection()
    try:
        refresh(conn, force=args.force)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
