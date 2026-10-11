"""
monitoring — replica sync health for the primary/replica Postgres pair.

The read replica follows the primary via physical WAL streaming: every
change — rows AND DDL — replays automatically. "Always in sync" is
therefore a monitoring problem, not a sync problem: detect a detached
or lagging replica, keep the replication slot from pinning unbounded
WAL on the primary, and gate deployments on replay catch-up.

Commands (run from the repo root; exit 0 = healthy, 1 = alert):

  python -m database.monitoring
      Health check — replication state, replay backlog, slot WAL
      retention, pg_wal dir size vs max_wal_size, PLUS downloads-stream
      liveness (--streams). Suitable for run_occasionally.sh / a
      scheduler.

  python -m database.monitoring --streams
      Stream liveness only — every downloads.stream.* poller and the
      stream_watchdog supervisor itself must have a fresh heartbeat in
      monitor.service_heartbeat.

  python -m database.monitoring --wait-catchup
                                  [--max-bytes N] [--timeout S] [--interval S]
      Post-DDL gate — block until the primary's replay backlog drains
      to --max-bytes (default 1MB) or --timeout (default 300s) elapses.
      Run after applying schema changes to database/sql/ before
      considering them deployed to the read path.

  python -m database.monitoring --schema-drift
      Tripwire — MD5 of the user-schema catalog (tables/partitions/
      views/matviews with full column lists) computed on BOTH nodes and
      compared. Physical replication makes a mismatch all-but-
      impossible; it catches the impossible cases (a stale re-seed, a
      version mismatch, manual writes against the replica port).

Reads connection parameters from database/.env via the shared
_common.db_commons helpers (never duplicate them).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time

import psycopg

from _common.db_commons._helpers import _get_conn_params, _get_replica_conn_params
from _common.service_heartbeat import (
    HEARTBEAT_TABLE,
    MONITORED_SERVICES,
    STALE_AFTER_S,
    WATCHDOG_SERVICE,
)

# Replay backlog (bytes the replica received but has not replayed) over
# which the replica counts as "busy" for the API read path — the SAME
# threshold as REPLICA_MAX_LAG_BYTES in data_viz/api/services/db.service.ts.
BACKLOG_ALERT_BYTES = 64 * 1024 * 1024

# WAL pinned by the physical slot while the replica is down/re-seeding.
# The primary's disk fills silently past this (the 2026-09 bulk-load
# incident froze the slot at ~29GB of retained WAL).
SLOT_RETAINED_ALERT_BYTES = 2 * 1024 * 1024 * 1024

# The pg_wal DIRECTORY idles under max_wal_size (recycled segments are kept
# for reuse), so an absolute alert would false-fire on healthy setups. The
# disk is in trouble when the dir GROWS past this factor × max_wal_size —
# segments are being pinned beyond the recycle horizon (an inactive slot,
# stuck replay/checkpoint), i.e. the silent-fill that precedes the
# slot-retention blowup above.
WAL_DIR_ALERT_FACTOR = 1.5

_WAL_SQL = """
SELECT (SELECT coalesce(sum(size), 0)::bigint FROM pg_ls_waldir()),
       pg_size_bytes(current_setting('max_wal_size'))::bigint
"""

_REPLICATION_SQL = """
SELECT state,
       pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::bigint AS backlog_bytes,
       pg_current_wal_lsn()::text AS primary_lsn
FROM pg_stat_replication
"""

_SLOT_SQL = """
SELECT slot_name, active,
       pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint AS retained_bytes
FROM pg_replication_slots
WHERE slot_type = 'physical'
"""

# Catalog fingerprint: one row, one hash. Tables, partitioned parents,
# partitions, views and matviews across ALL non-system schemas, keyed by
# qualified name with relkind + full ordered column list (name:type).
# autovacuum/ANALYZE/comment churn does not move it; any CREATE/ALTER/
# DROP of an object or column does.
_SCHEMA_HASH_SQL = """
WITH cols AS (
    SELECT a.attrelid,
           string_agg(a.attname || ':' || format_type(a.atttypid, a.atttypmod),
                      ';' ORDER BY a.attnum) AS col_list
    FROM pg_attribute a
    WHERE a.attnum > 0 AND NOT a.attisdropped
    GROUP BY a.attrelid
)
SELECT md5(string_agg(
    n.nspname || '.' || c.relname || ':' || c.relkind::text || ':' || coalesce(x.col_list, ''),
    ',' ORDER BY n.nspname, c.relname))
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN cols x ON x.attrelid = c.oid
WHERE n.nspname NOT LIKE 'pg\\_%'
  AND n.nspname <> 'information_schema'
  AND c.relkind IN ('r', 'p', 'v', 'm')
"""


def _connect(params: dict) -> psycopg.Connection:
    return psycopg.connect(
        host=params["host"],
        port=params["port"],
        dbname=params["database"],
        user=params["user"],
        password=params["password"],
        connect_timeout=10,
        autocommit=True,
    )


def _fmt_bytes(n: int) -> str:
    if n >= 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024 * 1024):.2f}GB"
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n}B"


def _fetch(conn: psycopg.Connection, sql: str) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def check_health() -> int:
    """Replica state + replay backlog + slot WAL retention + pg_wal dir
    size. 0/1 exit."""
    alerts: list[str] = []

    with _connect(_get_conn_params()) as primary:
        repl = _fetch(primary, _REPLICATION_SQL)
        if not repl:
            alerts.append(
                "[ALERT] primary has NO standby attached — pg_stat_replication is "
                "empty (replica container down or reconnecting). Reads still work "
                "but the replica is not protecting anything."
            )
        for state, backlog_bytes, primary_lsn in repl:
            if state != "streaming":
                alerts.append(
                    f"[ALERT] standby state is {state!r} (not streaming); "
                    f"primary_lsn={primary_lsn}"
                )
            if backlog_bytes > BACKLOG_ALERT_BYTES:
                alerts.append(
                    f"[ALERT] replay backlog {_fmt_bytes(backlog_bytes)} > "
                    f"{_fmt_bytes(BACKLOG_ALERT_BYTES)} — replica replaying a WAL "
                    f"burst; API reads fail over to the primary meanwhile"
                )
            else:
                print(
                    f"[ok] standby {state}, replay backlog {_fmt_bytes(backlog_bytes)} "
                    f"(primary_lsn={primary_lsn})"
                )

        slots = _fetch(primary, _SLOT_SQL)
        if not slots:
            alerts.append(
                "[ALERT] no physical replication slot on the primary — a restarted "
                "replica may never catch up (wal_keep_size=0 recycles segments)"
            )
        for slot_name, active, retained_bytes in slots:
            if not active:
                alerts.append(
                    f"[ALERT] slot {slot_name} is INACTIVE — WAL is being pinned on "
                    "the primary while the replica is down (E: disk fills silently)"
                )
            if retained_bytes > SLOT_RETAINED_ALERT_BYTES:
                alerts.append(
                    f"[ALERT] slot {slot_name} retains {_fmt_bytes(retained_bytes)} "
                    f"of WAL > {_fmt_bytes(SLOT_RETAINED_ALERT_BYTES)}"
                )
            else:
                print(
                    f"[ok] slot {slot_name} active={active}, "
                    f"retained WAL {_fmt_bytes(retained_bytes)}"
                )

        # WAL disk: pg_wal dir size vs the cluster's own recycle horizon
        # (max_wal_size). Recyclable segments legitimately keep the dir a
        # few GB; only growth beyond the horizon means something is pinned.
        wal_bytes, max_wal_bytes = _fetch(primary, _WAL_SQL)[0]
        wal_ceiling = int(max_wal_bytes * WAL_DIR_ALERT_FACTOR)
        if wal_bytes > wal_ceiling:
            alerts.append(
                f"[ALERT] pg_wal dir {_fmt_bytes(wal_bytes)} > "
                f"{WAL_DIR_ALERT_FACTOR}× max_wal_size "
                f"({_fmt_bytes(wal_ceiling)}) — segments are pinned beyond the "
                "recycle horizon; check the slot state above and disk space"
            )
        else:
            print(
                f"[ok] pg_wal dir {_fmt_bytes(wal_bytes)} "
                f"(alert ceiling {_fmt_bytes(wal_ceiling)} = "
                f"{WAL_DIR_ALERT_FACTOR}× max_wal_size {_fmt_bytes(max_wal_bytes)})"
            )

    for line in alerts:
        print(line)
    return 1 if alerts else 0


def check_streams() -> int:
    """Downloads-stream liveness: every poller + the watchdog supervisor
    must have a fresh heartbeat in monitor.service_heartbeat. 0/1 exit.

    Heartbeats are written by a daemon thread inside each process
    (_common/service_heartbeat.py) every ~60s regardless of the poll loop's
    sleep windows, so a stale beat means the process is dead or wedged —
    it never means "market closed". The watchdog (database/stream_watchdog.py)
    is included because a dead supervisor silently stops restarting
    pollers. The heartbeat rows of the repo's OTHER (batch) processes are
    NOT liveness-alerted here: they legitimately sit stopped between runs
    (their run history lives in monitor.service_audit; the watchdog's
    orphan pass closes unrecorded deaths). Restart counts read the same
    audit table's merged watchdog restart trail (dead lives closed with a
    'watchdog restart: …' exit_reason).
    """
    alerts: list[str] = []
    expected = list(MONITORED_SERVICES)

    with _connect(_get_conn_params()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT service, pid, "
                f"EXTRACT(EPOCH FROM (now() - beat_at))::bigint, "
                f"scope, sub_scope, stop_reason "
                f"FROM {HEARTBEAT_TABLE} WHERE service = ANY(%s)",
                (expected,),
            )
            beats = {
                svc: (pid, age, scope, sub, stop)
                for svc, pid, age, scope, sub, stop in cur.fetchall()
            }
            cur.execute(
                "SELECT service, count(*) FROM monitor.service_audit "
                "WHERE exit_reason LIKE 'watchdog restart%' "
                "AND ended_at > now() - interval '24 hours' "
                "GROUP BY service"
            )
            restarts_24h = dict(cur.fetchall())

    for service in expected:
        row = beats.get(service)
        label = "watchdog" if service == WATCHDOG_SERVICE else service
        if row is None:
            alerts.append(
                f"[ALERT] {label}: no heartbeat row — not running (or a "
                "pre-heartbeat build; restart it once to gain heartbeats)"
            )
            continue
        pid, age, scope, sub, stop_reason = row
        where = f"{scope}/{sub}" if scope and sub else (scope or "?")
        stop_note = f" — last exit: {stop_reason}" if stop_reason else ""
        if age > int(STALE_AFTER_S):
            alerts.append(
                f"[ALERT] {label} ({where}): heartbeat stale {age}s > "
                f"{int(STALE_AFTER_S)}s (pid {pid}) — dead or wedged"
                f"{stop_note}"
            )
        else:
            n = restarts_24h.get(service, 0)
            print(
                f"[ok] {label} ({where}): heartbeat {age}s ago (pid {pid})"
                + (f", {n} restart(s) in 24h" if n else "")
            )

    for line in alerts:
        print(line)
    return 1 if alerts else 0


def wait_catchup(max_bytes: int, timeout_s: float, interval_s: float) -> int:
    """Post-DDL gate: block until replay backlog drains to max_bytes."""
    deadline = time.monotonic() + timeout_s
    while True:
        with _connect(_get_conn_params()) as primary:
            repl = _fetch(primary, _REPLICATION_SQL)
        if not repl:
            print("[ALERT] no standby attached — nothing to wait for")
            return 1
        state, backlog_bytes, _ = repl[0]
        if backlog_bytes <= max_bytes:
            print(
                f"[ok] replica caught up: backlog {_fmt_bytes(backlog_bytes)} "
                f"<= {_fmt_bytes(max_bytes)} ({state})"
            )
            return 0
        if time.monotonic() >= deadline:
            print(
                f"[ALERT] backlog {_fmt_bytes(backlog_bytes)} still > "
                f"{_fmt_bytes(max_bytes)} after {timeout_s:.0f}s — replica still "
                "replaying; do NOT consider the DDL deployed to the read path"
            )
            return 1
        print(
            f"    waiting: backlog {_fmt_bytes(backlog_bytes)} "
            f"({state}), next poll in {interval_s:.0f}s"
        )
        time.sleep(interval_s)


def check_schema_drift() -> int:
    """Compare the catalog fingerprint between primary and replica."""
    with _connect(_get_conn_params()) as primary:
        primary_hash = _fetch(primary, _SCHEMA_HASH_SQL)[0][0]
    try:
        with _connect(_get_replica_conn_params()) as replica:
            replica_hash = _fetch(replica, _SCHEMA_HASH_SQL)[0][0]
    except Exception as e:  # noqa: BLE001 — any replica failure IS the alert
        print(f"[ALERT] replica unreachable for schema comparison: {e}")
        return 1
    if primary_hash != replica_hash:
        print(
            "[ALERT] schema drift between primary and replica — an object or "
            "column differs. If the replica just re-seeded, wait for catch-up "
            "and re-run; otherwise investigate before trusting replica reads."
        )
        print(f"    primary hash: {primary_hash}")
        print(f"    replica hash: {replica_hash}")
        return 1
    print(f"[ok] schemas identical (hash {primary_hash[:12]}…)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m database.monitoring",
        description="Replica sync health for the primary/replica Postgres pair.",
    )
    parser.add_argument(
        "--wait-catchup",
        action="store_true",
        help="post-DDL gate: wait until replay backlog drains, then exit",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=1024 * 1024,
        help="wait-catchup target backlog (default 1MB)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="wait-catchup budget in seconds (default 300)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="wait-catchup poll interval in seconds (default 2)",
    )
    parser.add_argument(
        "--schema-drift",
        action="store_true",
        help="compare the catalog fingerprint between primary and replica",
    )
    parser.add_argument(
        "--streams",
        action="store_true",
        help="downloads-stream liveness only (heartbeat freshness for all "
             "pollers + the stream_watchdog supervisor)",
    )
    args = parser.parse_args()
    from _common.service_heartbeat import autoregister  # lazy: keeps pandas behind the cudf hook
    autoregister()  # heartbeat + run audit (monitor.*)

    if args.schema_drift:
        return check_schema_drift()
    if args.wait_catchup:
        return wait_catchup(args.max_bytes, args.timeout, args.interval)
    if args.streams:
        return check_streams()
    # Default health: replica sync + stream liveness, both alert-only.
    rc = check_health()
    return check_streams() | rc


if __name__ == "__main__":
    sys.exit(main())
