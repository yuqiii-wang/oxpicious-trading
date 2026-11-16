"""sql_audit — always-on SQL statement audit sampler (pg_stat_statements).

The statement-level side of DB monitoring (database/db_health.py is the
instance side, database/db_table_audit.py the table side): every
``--interval`` (default 300s) it samples BOTH instances'
``pg_stat_statements`` — Postgres' own per-statement statistics,
literals normalized to ``$n`` placeholders, covering EVERY client
(pipelines, the data_viz API, the MCP server's arbitrary SQL, the
sandbox, psql) — and upserts one row per DISTINCT statement into
``monitor.sql_audit`` keyed by ``sql_hash`` = sha256 hex of the normalized
statement text. The same templated query with different parameters,
roles or connections is ONE audited identity.

The primary ships the extension preloaded (Supabase image); the replica
gets its preload appended by database/replica/entrypoint.sh so the API's
read half — served there — is audited too. An instance whose view is
missing or unreachable is SKIPPED this pass (warned once), and its cached
previous counters are KEPT so the next successful pass's deltas span the
gap truthfully.

Stats are DELTA-ACCUMULATED: each pass diffs the extension's cumulative
counters (calls, total_exec_time, rows) against the cached previous
sample per (instance, queryid, role) and ADDS the delta to the audit row,
so totals survive extension resets and LRU evictions — a counter DECREASE
means a reset, in which case the current value IS the delta since the
last sample. Executions on the primary and the replica are distinct real
executions of the same text and are summed; roles are merged per pass
(roles/instances columns carry the latest sample's set). The audit is
cumulative — no retention purge (bounded by distinct statements ever
seen).

    The per-pass counter cache lives in memory only. A restart cannot know
which executions the previous life already counted, so the FIRST pass of
a life loads the audit table's existing sql_hash set and treats known
statements as delta-zero (their counters resume accumulating from the
second pass) — only identities NOT yet in the table are seeded with
their full current counters (first_seen_at = that pass). ``--once`` is
exactly that first pass: a fresh install seeds everything; against an
already-populated table it only adds genuinely-new statements.

It is a service like the other samplers: registered under
``_common.service_heartbeat.MONITORED_SERVICES`` as ``database.sql_audit``
(scope database/stream), liveness-alerted by ``database/monitoring.py
--streams`` and restarted by the stream watchdog.

Run:

  python -u -m database.sql_audit            # foreground loop
  python -m database.sql_audit --once        # seeding pass and exit
  python -m database.sql_audit --interval 60
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from typing import Optional

import psycopg
from pydantic import BaseModel

from _common.db_commons import get_db_connection
from _common.db_commons._helpers import _get_conn_params, _get_replica_conn_params
from _common.labels import DB_INSTANCE_CODE
from _common.log_setup import setup_logging
from _common.service_heartbeat import register_service

logger = setup_logging("sql_audit")

SERVICE = "database.sql_audit"

# A sampler that hangs defeats itself — fail the snapshot after 5s and
# treat the instance as skipped this pass.
_CONNECT_TIMEOUT_S = 5

# One round trip per instance: every statement's cumulative counters,
# joined to its role and scoped to this database. queryid is PG's own
# normalized-parse-tree hash (cross-reference only — sql_hash is the
# identity); total_exec_time is in milliseconds.
_STATS_SQL = """
SELECT s.query, s.queryid::bigint, r.rolname,
       s.calls, s.total_exec_time, s.max_exec_time, s.rows
FROM pg_stat_statements s
JOIN pg_roles r ON r.oid = s.userid
WHERE s.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
"""

# Delta-accumulating upsert: new hashes INSERT their (delta == full)
# counters with first_seen_at; existing hashes ADD this pass's delta, keep
# the all-time max, and refresh avg / latest-sample attributes. avg_exec_ms
# is recomputed from the accumulated totals on the row itself.
_UPSERT_SQL = """
INSERT INTO monitor.sql_audit
    (sql_hash, sql_text, queryid, roles, instances,
     call_count, total_exec_ms, avg_exec_ms, max_exec_ms, rows_total,
     first_seen_at, last_active_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (sql_hash) DO UPDATE SET
    queryid = EXCLUDED.queryid,
    roles = EXCLUDED.roles,
    instances = EXCLUDED.instances,
    call_count = monitor.sql_audit.call_count + EXCLUDED.call_count,
    total_exec_ms = monitor.sql_audit.total_exec_ms + EXCLUDED.total_exec_ms,
    avg_exec_ms = (monitor.sql_audit.total_exec_ms + EXCLUDED.total_exec_ms)
                  / (monitor.sql_audit.call_count + EXCLUDED.call_count),
    max_exec_ms = GREATEST(monitor.sql_audit.max_exec_ms, EXCLUDED.max_exec_ms),
    rows_total = monitor.sql_audit.rows_total + EXCLUDED.rows_total,
    last_active_at = EXCLUDED.last_active_at
"""


class StatementRow(BaseModel):
    """One (query, role) cumulative-statistics row from one instance —
    the payload diffed between passes into a monitor.sql_audit upsert."""

    query: str
    queryid: int
    role: str
    calls: int
    total_ms: float
    max_ms: float
    rows: int


def _instance_params(instance: str) -> dict:
    """Connection params for an instance, from the shared helpers only
    (database/db_health.py precedent). Returns {} when the replica is not
    separately configured: a second sample of the same node would be a
    lie."""
    if instance == "primary":
        return _get_conn_params()
    if not os.environ.get("SUPABASE_REPLICA_HOST"):
        return {}
    return _get_replica_conn_params()


def _connect(params: dict) -> psycopg.Connection:
    return psycopg.connect(
        host=params["host"],
        port=params["port"],
        dbname=params["database"],
        user=params["user"],
        password=params["password"],
        connect_timeout=_CONNECT_TIMEOUT_S,
        autocommit=True,
    )


class _HashAgg:
    """This pass's delta aggregation for one sql_hash: summed deltas over
    roles/instances plus the latest-sample attributes written with them.
    ``seeded`` marks counters taken with NO cached previous sample (full
    current totals) — the first-pass known-hash guard zeroes those for
    statements a previous sampler life already counted."""

    def __init__(self, sql_text: str) -> None:
        self.sql_text = sql_text
        self.queryid: int = 0
        self.roles: set[str] = set()
        self.instances: set[str] = set()
        self.calls = 0
        self.total_ms = 0.0
        self.max_ms = 0.0
        self.rows = 0
        self.seeded = False

    def add(self, queryid: int, role: str, instance: str,
            calls: int, total_ms: float, max_ms: float, rows: int,
            seeded: bool) -> None:
        self.queryid = queryid
        self.roles.add(role)
        self.instances.add(instance)
        self.calls += calls
        self.total_ms += total_ms
        self.max_ms = max(self.max_ms, max_ms)
        self.rows += rows
        self.seeded = self.seeded or seeded


class SqlAuditSampler:
    """The SQL statement audit service object: samples both instances'
    pg_stat_statements each pass, diffs against the cached previous
    counters per (instance, queryid, role), and upserts delta-accumulated
    rows into monitor.sql_audit keyed by sha256 of the statement text.

    Per-life in-memory state is the previous pass's counters per
    instance. A restart cannot know what the previous life counted, so
    this life's FIRST pass loads the audit table's existing identities
    and zeroes the seeded counters of anything already known (see
    _HashAgg.seeded) — restarts are non-inflating; only genuinely-new
    statements enter the table at boot.
    """

    def __init__(self) -> None:
        # instance -> {(queryid, role) -> (calls, total_ms, max_ms, rows)}
        self._prev: dict[str, dict[tuple[int, str], tuple[int, float, float, int]]] = {}
        # instances already warned about a missing pg_stat_statements view
        self._warned_missing: set[str] = set()
        # True until this life's first completed pass — the pass that
        # reconciles in-memory (empty) counters against the audit table
        # via the known-hash guard below.
        self._first_pass = True

    # -- sampling -----------------------------------------------------------

    def _fetch(self, instance: str) -> Optional[list[StatementRow]]:
        """One round trip against the instance -> its statements' counters,
        or None when the extension view is missing / unreachable (the
        cached previous counters are kept; the next good pass's deltas
        span the gap)."""
        params = _instance_params(instance)
        if not params:
            logger.info("[skip] %s: SUPABASE_REPLICA_HOST unset — no replica configured", instance)
            return None
        try:
            conn = _connect(params)
            try:
                with conn.cursor() as cur:
                    cur.execute(_STATS_SQL)
                    rows = cur.fetchall()
            finally:
                conn.close()
        except psycopg.errors.UndefinedTable:
            if instance not in self._warned_missing:
                self._warned_missing.add(instance)
                logger.warning(
                    "[ALERT] %s: pg_stat_statements view missing — extension not "
                    "created/preloaded there; instance skipped every pass until fixed",
                    instance,
                )
            return None
        except Exception as e:  # noqa: BLE001 — the sampler must outlive its target
            logger.error("[ALERT] %s: snapshot failed (%s: %s) — skipped this pass",
                         instance, type(e).__name__, str(e)[:200])
            return None
        return [StatementRow(
            query=r[0], queryid=r[1], role=r[2],
            calls=r[3], total_ms=float(r[4]), max_ms=float(r[5]), rows=r[6],
        ) for r in rows]

    # -- pass ---------------------------------------------------------------

    def _known_hashes(self) -> set[str]:
        """The audit table's existing identities — the first pass of a
        life uses them to zero the seeded counters of statements a previous
        life already counted (see _HashAgg.seeded)."""
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT sql_hash FROM monitor.sql_audit")
                return {str(r[0]) for r in cur.fetchall()}
        finally:
            conn.close()

    def run_pass(self) -> None:
        """One pass: sample both instances, diff, upsert delta rows. Never
        raises — a failed pass is logged and retried next interval."""
        try:
            now = time.time()
            by_hash: dict[str, _HashAgg] = {}
            for instance in DB_INSTANCE_CODE:
                rows = self._fetch(instance)
                if rows is None:
                    continue
                prev = self._prev.get(instance, {})
                new_prev: dict[tuple[int, str], tuple[int, float, float, int]] = {}
                for row in rows:
                    key = (row.queryid, row.role)
                    counters = (row.calls, row.total_ms, row.max_ms, row.rows)
                    new_prev[key] = counters
                    p = prev.get(key)
                    # delta vs the previous sample: no previous (this
                    # statement is new since the last pass — or since the
                    # sampler started) or a DECREASE (the extension
                    # reset/evicted) means the current value IS the delta
                    # since the last pass.
                    d_calls = row.calls if p is None or row.calls < p[0] else row.calls - p[0]
                    if d_calls <= 0:
                        continue
                    if p is None or row.calls < p[0]:
                        d_total, d_rows = row.total_ms, row.rows
                    else:
                        d_total = row.total_ms - p[1]
                        d_rows = row.rows - p[3]
                    h = hashlib.sha256(row.query.encode("utf-8")).hexdigest()
                    agg = by_hash.get(h)
                    if agg is None:
                        agg = by_hash[h] = _HashAgg(row.query)
                    agg.add(row.queryid, row.role, instance,
                            d_calls, d_total, row.max_ms, d_rows, seeded=p is None)
                # state advances only on a SUCCESSFUL sample — the next
                # pass's deltas then span any outage gap truthfully.
                self._prev[instance] = new_prev

            known: set[str] = set()
            if self._first_pass:
                known = self._known_hashes()
            upserts = []
            for h, a in sorted(by_hash.items()):
                if a.seeded and h in known:
                    # a previous sampler life already counted these
                    # counters — resume from the NEXT pass instead of
                    # adding them again (restart inflation guard)
                    continue
                upserts.append((
                    h, a.sql_text, a.queryid, ",".join(sorted(a.roles)),
                    ",".join(sorted(a.instances)), a.calls, round(a.total_ms, 3),
                    round(a.total_ms / a.calls, 3), round(a.max_ms, 3), a.rows,
                    # first_seen_at only lands on INSERT (the conflict
                    # branch does not touch it); last_active_at is this pass
                    time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now)),
                    time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now)),
                ))
            conn = get_db_connection()
            try:
                with conn.cursor() as cur:
                    if upserts:
                        cur.executemany(_UPSERT_SQL, upserts)
            finally:
                conn.close()
            self._first_pass = False
            logger.info("[ok] pass: %d statement(s) active, %d upserted%s",
                        len(by_hash), len(upserts),
                        ", %d already-known seed(s) skipped" % (len(by_hash) - len(upserts))
                        if len(upserts) != len(by_hash) else "")
        except Exception as e:  # noqa: BLE001 — the sampler must outlive its target
            logger.error("[ALERT] pass failed (%s: %s) — this pass lost; "
                         "will retry next interval", type(e).__name__, e)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m database.sql_audit",
        description="Always-on SQL statement audit: pg_stat_statements deltas "
                    "per (instance, queryid, role), upserted into "
                    "monitor.sql_audit keyed by sha256 of the statement text.",
    )
    parser.add_argument(
        "--interval", type=float, default=300.0,
        help="seconds between sample passes (default 300)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="one seeding pass and exit (no heartbeat registration; with no "
             "cached counters, every live statement's FULL current totals "
             "are recorded with first_seen_at = now)",
    )
    args = parser.parse_args()

    sampler = SqlAuditSampler()

    if args.once:
        sampler.run_pass()
        return 0

    # Explicit scope: the service left MONITORED_SERVICES 2026-11 (a
    # monitor-area resident now), so the registry no longer supplies it.
    register_service(SERVICE, scope="database", sub_scope="stream")
    logger.info("sql_audit sampler started (interval %.0fs)", args.interval)
    while True:
        started = time.monotonic()
        sampler.run_pass()
        # Sleep the REMAINDER so pass time does not stretch the cadence.
        elapsed = time.monotonic() - started
        time.sleep(max(0.0, args.interval - elapsed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
