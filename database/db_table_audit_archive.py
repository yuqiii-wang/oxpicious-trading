"""db_table_audit_archive — per-day rollup of the per-table workload audit.

Rebuilds ``monitor.db_table_audit_archive`` (database/sql/monitoring/
db_table_audit_archive.sql) from ``monitor.db_table_audit``: ONE record per
(GMT day, table, instance) — the day's activity-sparse pass-rows collapsed
into day sums of row/scan deltas, peak write/read rates, the worst
write_flag, and end-of-day size/live-tup snapshots. The audit table is the
source of truth; this table is COMPUTED — never hand-written, never read
as fresh data. No retention purge: this table IS the retention (the audit
keeps ~30d of per-pass detail; the archive keeps the daily history).

Commands:

  python -m database.db_table_audit_archive
      Incremental refresh (the default): a DATE is stale — and is
      recomputed (its archive rows purged + re-aggregated, two statements,
      one transaction) — when the audit gained rows after the date's last
      computed_at. The current partial day refreshes on every run and
      converges once the day closes (the GMT rollover, Beijing 08:00);
      missed days self-heal. This is the idempotent form of "aggregate the
      last 24h" — the daily maintainer pass (server_cluster/maintainer/
      jobs.py DbTableAuditArchiveJob, 03:30 Asia/Shanghai) runs exactly
      this.

  python -m database.db_table_audit_archive --force
      Purge the whole archive table and re-aggregate every audit date.

Pure SQL aggregation — no dataframes, so no cudf activation; the per-date
DELETE+INSERT is one chunk per partition-key date with the 0.1s
between-chunk pause (the bulk-write rule, at the size where it costs
nothing — database/external_resource_stats.py, whose shape this module
mirrors). Connection from _common.db_commons (database/.env).

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

# The house "operational day": UTC calendar day of checked_at (Beijing
# 08:00 boundary) — same convention as llm.llm_requests' generated date.
_DAY_EXPR = "(checked_at AT TIME ZONE 'utc')::date"

# Dates whose audit rows are newer than the date's last archive rebuild
# (or that have no archive rows at all), oldest first.
_STALE_DATES_SQL = f"""
SELECT d.date, d.last_row_at, s.computed_at
FROM (
    SELECT {_DAY_EXPR} AS date, max(checked_at) AS last_row_at
    FROM monitor.db_table_audit
    GROUP BY 1
) d
LEFT JOIN (
    SELECT date, max(computed_at) AS computed_at
    FROM monitor.db_table_audit_archive
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
DELETE FROM monitor.db_table_audit_archive WHERE date = %s
"""

# The day collapsed per (instance, table): NULL-safe sums (a baseline
# census pass carries NULL deltas), max() rates/flags, and the day's LAST
# pass snapshots via array_agg ordered DESC (an ORDER BY inside max() is
# not a thing; the first element of the descending array is).
_AGGREGATE_DATE_SQL = f"""
INSERT INTO monitor.db_table_audit_archive
    (date, instance, table_schema, table_name,
     active_passes, ins_rows, upd_rows, del_rows,
     peak_write_rate, peak_read_rate,
     seq_scan, idx_scan, seq_tup_read, idx_tup_fetch,
     max_write_flag, flagged_passes,
     end_n_live_tup, end_n_dead_tup, end_table_size_bytes,
     first_active_at, last_active_at, computed_at)
SELECT {_DAY_EXPR}, instance, table_schema, table_name,
       count(*),
       coalesce(sum(ins_rows), 0), coalesce(sum(upd_rows), 0),
       coalesce(sum(del_rows), 0),
       max(write_rate), max(read_rate),
       coalesce(sum(seq_scan), 0), coalesce(sum(idx_scan), 0),
       coalesce(sum(seq_tup_read), 0), coalesce(sum(idx_tup_fetch), 0),
       max(write_flag),
       count(*) FILTER (WHERE write_flag > 0),
       (array_agg(n_live_tup ORDER BY checked_at DESC))[1],
       (array_agg(n_dead_tup ORDER BY checked_at DESC))[1],
       (array_agg(table_size_bytes ORDER BY checked_at DESC))[1],
       min(checked_at), max(checked_at), now()
FROM monitor.db_table_audit
WHERE {_DAY_EXPR} = %s
GROUP BY 1, instance, table_schema, table_name
"""


def _fmt_ts(ts: object) -> str:
    return ts.isoformat(sep=" ", timespec="seconds") if ts else "-"  # type: ignore[union-attr]


def refresh(conn: Connection, *, force: bool = False) -> tuple[int, int]:
    """Refresh the archive table; returns (dates rebuilt, archive rows
    written)."""
    if force:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE monitor.db_table_audit_archive")
        print("[db_table_audit_archive] --force: purged the whole table")

    with conn.cursor() as cur:
        cur.execute(_STALE_DATES_SQL)
        stale = cur.fetchall()

    if not stale:
        print("[db_table_audit_archive] no stale dates — archive is current")
        return 0, 0

    rebuilt = 0
    written = 0
    for date, last_row_at, computed_at in stale:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(_PURGE_DATE_SQL, (date,))
                cur.execute(_AGGREGATE_DATE_SQL, (date,))
                n = cur.rowcount
        written += n
        rebuilt += 1
        print(
            f"[db_table_audit_archive] rebuilt {date}: {n} "
            f"row(s) (audit max {_fmt_ts(last_row_at)} > "
            f"archive {_fmt_ts(computed_at)})"
        )
        if rebuilt < len(stale):
            time.sleep(_CHUNK_PAUSE_S)

    # The incremental write is tiny, but a first build / --force rebuild
    # can cross the one-commit-chunk scale — keep planner stats fresh.
    with conn.cursor() as cur:
        cur.execute("ANALYZE monitor.db_table_audit_archive")

    print(f"[db_table_audit_archive] done — {rebuilt} date(s) rebuilt, "
          f"{written} archive row(s)")
    return rebuilt, written


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m database.db_table_audit_archive",
        description="Rebuild monitor.db_table_audit_archive from the "
                    "per-table workload audit (one record per GMT day / "
                    "table / instance).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="purge the whole archive table and re-aggregate every audit date",
    )
    args = parser.parse_args()

    # Heartbeat + run audit (monitor.*) — lazy so the module stays import-
    # light for the maintainer job that only wants refresh().
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
