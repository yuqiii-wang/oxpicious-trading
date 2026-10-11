"""db_health — always-on DB health sampler for the primary/replica pair.

database/monitoring.py answers "is the cluster healthy RIGHT NOW" as
exit-code alerts; this module is the HISTORY side: every ``--interval``
(default 60s) it samples BOTH Postgres instances — reachable? how fast?
DB-internal vitals (uptime, connections, size, cache hit ratio,
xact_commit, WAL shipping state, replay backlog, pg_wal dir size) plus
the container's CPU/memory from ``docker stats`` — and appends one row
per instance to ``monitor.db_health``. An unreachable instance is
SAMPLED TOO (healthy=false + a short error; container stats filled when
Docker still reports it) so outages leave a trail instead of a gap.

It is a service like the downloads pollers: registered under
``_common.service_heartbeat.MONITORED_SERVICES`` as
``database.db_health`` (scope database/stream, the always-on services),
so a dead or wedged sampler is liveness-alerted by
``database/monitoring.py --streams`` and restarted by the stream
watchdog like any poller.

Instance identity comes from the shared ``_common.db_commons`` helpers
(never hand-built): the primary from SUPABASE_HOST/PORT, the replica from
SUPABASE_REPLICA_HOST/PORT — when the replica host is unset (single-node
deployments) the replica sample is SKIPPED rather than written as a
second row of the same node.

Sampling connections are short-timeout direct connects built on the
helpers' params (``database/monitoring.py`` precedent): the router pools
route by read/write class, while a health probe must hit a SPECIFIC
instance, including a replica that just refused a connection. Writes go
to the primary through ``get_db_connection()`` — two rows per pass, plain
INSERT rung.

Rows live in the DB being sampled: while the primary itself is down the
pass degrades to [ALERT] log lines (logs/<date>/db_health.log) and
resumes on recovery — the same degradation the stream watchdog accepts
for its own decisions.

Run:

  python -u -m database.db_health              # foreground loop
  python -m database.db_health --once          # single sample pass
  python -m database.db_health --interval 30 --retain-days 90
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time

import psycopg

from _common.db_commons import get_db_connection
from _common.db_commons._helpers import _get_conn_params, _get_replica_conn_params
from _common.labels import DB_INSTANCE_CODE, DB_REPLICATION_STATE_CODE
from _common.log_setup import setup_logging
from _common.service_heartbeat import register_service

logger = setup_logging("db_health")

SERVICE = "database.db_health"

# (instance, container) — the docker-compose service names. A container
# name is per-deployment; override via DB_HEALTH_CONTAINERS as
# "primary=name,replica=name" if the cluster is renamed.
_CONTAINER = {
    "primary": "oxpicious-db",
    "replica": "oxpicious-db-replica",
}

# Probe timeout: a health sampler that hangs defeats itself — fail the
# sample after 5s and record it as unreachable instead.
_CONNECT_TIMEOUT_S = 5

_DOCKER_STATS_TIMEOUT_S = 30

# A deleted/retired sample's TTL, enforced each pass (2 rows/min — the
# delete is an index-tail trim, not a purge job).
_DEFAULT_RETAIN_DAYS = 90

_RETENTION_SQL = """
DELETE FROM monitor.db_health
WHERE checked_at < now() - make_interval(days => %s)
"""

_INSERT_SQL = """
INSERT INTO monitor.db_health
    (instance, healthy, connect_ms, error,
     uptime_s, active_connections, db_size_bytes, cache_hit_ratio,
     xact_commit, replication_state, replay_backlog_bytes,
     cpu_pct, mem_used_bytes, mem_limit_bytes, mem_pct, wal_dir_bytes)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

# One round trip per instance. The replication legs differ by role:
# primary reads the STANDBY's sender row (most-behind first — there is
# exactly one physical replica today), replica reads its own receiver +
# local receive-vs-replay gap. wal_dir_bytes is the instance's own pg_wal
# directory size (recycled segments included — it legitimately idles under
# max_wal_size; growth beyond ~1.5× max_wal_size means segments are pinned).
_VITALS_SQL = """
WITH d AS (SELECT * FROM pg_stat_database WHERE datname = current_database())
SELECT EXTRACT(EPOCH FROM (now() - pg_postmaster_start_time()))::bigint,
       d.numbackends,
       pg_database_size(current_database()),
       CASE WHEN d.blks_hit + d.blks_read > 0
            THEN d.blks_hit::numeric / (d.blks_hit + d.blks_read) END,
       d.xact_commit,
       {repl_state_sql},
       {backlog_sql},
       (SELECT coalesce(sum(size), 0)::bigint FROM pg_ls_waldir())
FROM d
"""

_REPL_LEGS = {
    "primary": (
        "(SELECT state FROM pg_stat_replication ORDER BY replay_lsn DESC NULLS LAST LIMIT 1)",
        "(SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::bigint "
        "FROM pg_stat_replication ORDER BY replay_lsn DESC NULLS LAST LIMIT 1)",
    ),
    "replica": (
        "(SELECT status FROM pg_stat_wal_receiver)",
        "(SELECT pg_wal_lsn_diff(pg_last_wal_receive_lsn(), "
        "pg_last_wal_replay_lsn())::bigint)",
    ),
}

_DOCKER_STATS_CMD = (
    "docker", "stats", "--no-stream",
    "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}",
)

_SIZE_RE = re.compile(r"^([\d.]+)\s*(B|KiB|MiB|GiB|TiB)$")
_SIZE_MULT = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}


def _instance_params(instance: str) -> dict:
    """Connection params for an instance, from the shared helpers only.

    Returns {} when the replica is not separately configured — the
    helpers' fallback points at the primary and a second row for the same
    node would be a lie.
    """
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


def _state_code(state: str | None) -> int:
    return DB_REPLICATION_STATE_CODE[state] if state else DB_REPLICATION_STATE_CODE["none"]


def sample_instance(instance: str, containers: dict[str, dict]) -> tuple | None:
    """Sample one instance -> an _INSERT_SQL row tuple, or None to skip.

    Never raises: every failure mode collapses into healthy=false +
    error text (the sampler itself must outlive everything it probes).
    """
    params = _instance_params(instance)
    if not params:
        logger.info("[skip] %s: SUPABASE_REPLICA_HOST unset — no replica configured", instance)
        return None

    container = containers.get(_CONTAINER[instance])
    row: list = [
        DB_INSTANCE_CODE[instance],
        False,
        None,
        None,
        None, None, None, None,
        None, None, None,
        container.get("cpu_pct") if container else None,
        container.get("mem_used_bytes") if container else None,
        container.get("mem_limit_bytes") if container else None,
        container.get("mem_pct") if container else None,
        None,  # wal_dir_bytes — filled from the vitals query
    ]

    t0 = time.monotonic()
    try:
        conn = _connect(params)
        try:
            with conn.cursor() as cur:
                cur.execute(_VITALS_SQL.format(
                    repl_state_sql=_REPL_LEGS[instance][0],
                    backlog_sql=_REPL_LEGS[instance][1],
                ))
                vitals = cur.fetchone()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — an unreachable instance IS a sample
        row[3] = f"{type(e).__name__}: {str(e)[:200]}"
        logger.error("[ALERT] %s: %s", instance, row[3])
        return tuple(row)

    row[2] = int((time.monotonic() - t0) * 1000)
    (uptime_s, backends, db_size, cache_ratio, xact_commit, state, backlog,
     wal_bytes) = vitals
    row[4:11] = [uptime_s, backends, db_size, cache_ratio, xact_commit,
                 _state_code(state), backlog]
    row[15] = wal_bytes

    # streaming is the only healthy shipping state (mirrors
    # database/monitoring.py check_health: any other state — including
    # 'none', i.e. no standby attached — is an alert there and here).
    row[1] = row[9] == DB_REPLICATION_STATE_CODE["streaming"]
    if not row[1]:
        logger.error(
            "[ALERT] %s: reachable but WAL shipping state is %r (backlog=%s)",
            instance, state, backlog,
        )
    else:
        logger.info(
            "[ok] %s: %dms, %d conns, backlog=%sB, wal_dir=%sB, cpu=%s%%, mem=%sB (%s%%)",
            instance, row[2], backends, backlog, wal_bytes,
            container.get("cpu_pct") if container else "?",
            container.get("mem_used_bytes") if container else "?",
            container.get("mem_pct") if container else "?",
        )
    return tuple(row)


def sample_containers() -> dict[str, dict]:
    """One `docker stats --no-stream` call for both DB containers.

    Docker lives on the Windows host; the CLI is reachable from WSL. Any
    failure (CLI missing, daemon down, timeout) degrades to no container
    metrics — the DB-internal vitals are the sample's core.
    """
    try:
        proc = subprocess.run(
            _DOCKER_STATS_CMD, capture_output=True, text=True,
            timeout=_DOCKER_STATS_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("docker stats unavailable (%s: %s) — container metrics skipped",
                       type(e).__name__, e)
        return {}
    out: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        name, cpu_s, mem_s, memp_s = (p.strip() for p in parts)
        used_s, _, limit_s = mem_s.partition("/")
        try:
            m = _SIZE_RE.match(used_s.strip())
            l = _SIZE_RE.match(limit_s.strip())
            out[name] = {
                "cpu_pct": float(cpu_s.rstrip("%")),
                "mem_used_bytes": int(float(m.group(1)) * _SIZE_MULT[m.group(2)]) if m else None,
                "mem_limit_bytes": int(float(l.group(1)) * _SIZE_MULT[l.group(2)]) if l else None,
                "mem_pct": float(memp_s.rstrip("%")),
            }
        except ValueError:
            logger.warning("docker stats: unparseable row %r", line)
    return out


def run_pass(retain_days: int) -> None:
    """One sample pass: container stats + one row per instance, appended
    atomically; retention trimmed first. Never raises."""
    try:
        containers = sample_containers()
        rows = [
            r for inst in DB_INSTANCE_CODE
            if (r := sample_instance(inst, containers)) is not None
        ]
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                if retain_days > 0:
                    cur.execute(_RETENTION_SQL, (retain_days,))
                if rows:
                    cur.executemany(_INSERT_SQL, rows)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — the sampler must outlive its target
        logger.error("[ALERT] sample pass failed (%s: %s) — rows for this pass lost; "
                     "will retry next interval", type(e).__name__, e)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m database.db_health",
        description="Always-on health/CPU/mem sampler for the primary/replica "
                    "Postgres pair, appending to monitor.db_health.",
    )
    parser.add_argument(
        "--interval", type=float, default=60.0,
        help="seconds between sample passes (default 60)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="one sample pass and exit (no heartbeat registration)",
    )
    parser.add_argument(
        "--retain-days", type=int, default=_DEFAULT_RETAIN_DAYS,
        help="purge samples older than this many days; 0 disables (default 90)",
    )
    args = parser.parse_args()

    if args.once:
        run_pass(args.retain_days)
        return 0

    register_service(SERVICE)  # heartbeat + audit row (monitor.*)
    logger.info("db_health sampler started (interval %.0fs, retain %dd)",
                args.interval, args.retain_days)
    while True:
        started = time.monotonic()
        run_pass(args.retain_days)
        # Sleep the REMAINDER of the interval so pass time (docker stats
        # can take seconds) does not stretch the cadence.
        elapsed = time.monotonic() - started
        time.sleep(max(0.0, args.interval - elapsed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
