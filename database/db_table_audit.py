"""db_table_audit — always-on per-table DB workload audit + write-heavy alerts.

The per-table HISTORY side of DB monitoring (database/db_health.py is the
per-INSTANCE side): every ``--interval`` (default 300s) it snapshots BOTH
Postgres instances' ``pg_stat_user_tables`` cumulative counters — scans,
tuples read/fetched, tuples inserted/updated/deleted, live/dead tuples,
relation size — and appends ACTIVITY-SPARSE rows to
``monitor.db_table_audit`` carrying the DELTA against the previous pass
as rates (rows/s) and delta counts. A row is written only for tables that
were ACTIVE in the window (any counter moved) or whose write_flag CHANGED
(so a clear is always recorded); a baseline pass after a sampler
(re)start writes one census row per table (NULL rates + size/live-tup
snapshot). Idle tables write nothing — with ~1500 tables a dense
per-pass row set would itself be the heaviest write load in the DB; the
absence of rows between two checked_at stamps IS the idle signal, and
"latest row per table" reads as "last active pass". The counters are
cumulative since the last stats reset, so consecutive-pass diffs are the
window's workload; an unknowable delta (first pass after a sampler
(re)start, a counter DECREASE from a stats reset, a new table's first
pass) writes NULL rates and re-baselines on the next pass. An unreachable
instance is SKIPPED, not gap-filled — its previous snapshot is kept, so
the next successful pass's window simply spans the gap and the rates stay
truthful.

The primary row is the write-side truth (all writes go to the primary);
the replica row carries the API's read load plus its WAL-replay apply.
Both are sampled so the read audit sees reads where they are served.

Write-heavy ALERTING (primary rows only): write_rate —
(inserts+updates+deletes rows)/s over the window — is judged against the
FIXED amber/red thresholds (module constants, env-overridable via
DB_TABLE_WRITE_AMBER_RPS / DB_TABLE_WRITE_RED_RPS). The verdict rides
every audit row as ``write_flag`` (label domain 'db_write_flag'), and
each STATE TRANSITION (ok→amber→red→…→ok) is appended to
``monitor.db_table_alert`` — the monitor log — and logged
([ALERT] on a raise, [ok] on a clear). Transitions, not passes: a table
sitting red for an hour writes one raise row, not twelve. After a restart
the in-memory previous-flag state re-baselines at ok, so a still-red
table logs one fresh raise row — the trail stays truthful.

Like db_health it is a service: registered under
``_common.service_heartbeat.MONITORED_SERVICES`` as
``database.db_table_audit`` (scope database/stream), liveness-alerted by
``database/monitoring.py --streams`` and restarted by the stream
watchdog. The per-pass delta state lives in memory only (one lost window
per restart — the same degradation db_health accepts for its samples);
``--once`` therefore writes a BASELINE pass (NULL rates), useful as a
smoke test and for first-time boot.

Run:

  python -u -m database.db_table_audit            # foreground loop
  python -m database.db_table_audit --once        # baseline pass and exit
  python -m database.db_table_audit --interval 60 --retain-days 30
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

import psycopg
from pydantic import BaseModel

from _common.db_commons import get_db_connection
from _common.db_commons._helpers import _get_conn_params, _get_replica_conn_params
from _common.labels import DB_INSTANCE_CODE, DB_WRITE_FLAG_CODE
from _common.log_setup import setup_logging
from _common.service_heartbeat import register_service

logger = setup_logging("db_table_audit")

SERVICE = "database.db_table_audit"

# ---------------------------------------------------------------------------
#  Fixed write-heavy thresholds (rows/s of ins+upd+del, averaged over the
#  sample window). Env-overridable for test bursts and re-tuning without a
#  code edit; raising them does not rewrite the alert trail (each alert row
#  records the threshold that was in effect).
# ---------------------------------------------------------------------------
AMBER_WRITE_RPS = 100.0
RED_WRITE_RPS = 1000.0

# Retention: audit rows ~2 instances × tables × 288 passes/day — 30 days
# is plenty of trend; the alert trail is a handful of rows per day, keep 90.
_DEFAULT_RETAIN_DAYS = 30
_DEFAULT_ALERT_RETAIN_DAYS = 90

# A health sampler that hangs defeats itself — fail the snapshot after 5s
# and treat the instance as skipped this pass.
_CONNECT_TIMEOUT_S = 5

_RETENTION_AUDIT_SQL = """
DELETE FROM monitor.db_table_audit
WHERE checked_at < now() - make_interval(days => %s)
"""

_RETENTION_ALERT_SQL = """
DELETE FROM monitor.db_table_alert
WHERE raised_at < now() - make_interval(days => %s)
"""

_INSERT_AUDIT_SQL = """
INSERT INTO monitor.db_table_audit
    (instance, table_schema, table_name, window_s,
     write_rate, ins_rows, upd_rows, del_rows,
     read_rate, seq_scan, idx_scan, seq_tup_read, idx_tup_fetch,
     n_live_tup, n_dead_tup, table_size_bytes, write_flag)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_INSERT_ALERT_SQL = """
INSERT INTO monitor.db_table_alert
    (table_schema, table_name, severity, write_rate, threshold_rate,
     window_s, message)
VALUES (%s, %s, %s, %s, %s, %s, %s)
"""

# One round trip per instance: every user table's cumulative counters +
# total relation size. pg_stat_user_tables already excludes system
# schemas; order is irrelevant (rows are keyed by (schema, name) in
# memory).
_STATS_SQL = """
SELECT schemaname, relname,
       seq_scan, seq_tup_read, idx_scan, idx_tup_fetch,
       n_tup_ins, n_tup_upd, n_tup_del,
       n_live_tup, n_dead_tup,
       pg_total_relation_size(format('%I.%I', schemaname, relname))
FROM pg_stat_user_tables
"""

# Cumulative counters a DECREASE in any of which means the stats backend
# reset (restart, pg_stat_reset) — deltas vs the cached snapshot would be
# negative garbage, so that pass re-baselines instead.
_COUNTER_FIELDS = (
    "seq_scan", "seq_tup_read", "idx_scan", "idx_tup_fetch",
    "n_tup_ins", "n_tup_upd", "n_tup_del",
)


class TableSnapshot(BaseModel):
    """One table's cumulative pg_stat_user_tables counters at a pass —
    the payload diffed between passes into a monitor.db_table_audit row."""

    table_schema: str
    table_name: str
    seq_scan: int
    seq_tup_read: int
    idx_scan: int
    idx_tup_fetch: int
    n_tup_ins: int
    n_tup_upd: int
    n_tup_del: int
    n_live_tup: int
    n_dead_tup: int
    table_size_bytes: int


def _env_float(name: str, default: float) -> float:
    """Threshold from the environment, falling back to the default (with a
    warning) on a bad value — a typo must not silently disable alerting."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("[ALERT] %s=%r is not a number — using default %s",
                       name, raw, default)
        return default
    if value < 0:
        logger.warning("%s=%r is negative — using default %s", name, raw, default)
        return default
    return value


def _instance_params(instance: str) -> dict:
    """Connection params for an instance, from the shared helpers only
    (database/db_health.py precedent — the router pools cannot target a
    SPECIFIC instance). Returns {} when the replica is not separately
    configured: a second sample of the same node would be a lie."""
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


class DbTableAuditSampler:
    """The per-table workload audit service object: fetches both
    instances' cumulative table counters each pass, diffs against the
    cached previous pass, appends monitor.db_table_audit rows, and logs
    write-flag transitions to monitor.db_table_alert.

    Per-life in-memory state: the previous pass's snapshots + sample time
    per instance, and the previous evaluated flag per table (for primary
    rows). A restart loses one window (NULL-rates baseline pass) and
    re-baselines the flags at ok — a still-flagged table then logs one
    fresh raise, keeping the trail truthful.
    """

    def __init__(
        self,
        *,
        amber_rps: float = AMBER_WRITE_RPS,
        red_rps: float = RED_WRITE_RPS,
    ) -> None:
        self._amber_rps = amber_rps
        self._red_rps = red_rps
        # instance -> monotonic time of its last SUCCESSFUL sample
        self._last_sample_at: dict[str, float] = {}
        # instance -> {(schema, table) -> TableSnapshot} of that sample
        self._prev: dict[str, dict[tuple[str, str], TableSnapshot]] = {}
        # (schema, table) -> last evaluated write_flag code (primary only)
        self._flags: dict[tuple[str, str], int] = {}

    # -- sampling -----------------------------------------------------------

    def _fetch_snapshots(self, instance: str) -> Optional[list[TableSnapshot]]:
        """One round trip against the instance -> its tables' counters, or
        None when unreachable (previous state is KEPT so the next pass's
        window spans the gap)."""
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
        except Exception as e:  # noqa: BLE001 — the sampler must outlive its target
            logger.error("[ALERT] %s: snapshot failed (%s: %s) — instance skipped this pass",
                         instance, type(e).__name__, str(e)[:200])
            return None
        return [TableSnapshot(
            table_schema=r[0], table_name=r[1],
            seq_scan=r[2], seq_tup_read=r[3], idx_scan=r[4], idx_tup_fetch=r[5],
            n_tup_ins=r[6], n_tup_upd=r[7], n_tup_del=r[8],
            n_live_tup=r[9], n_dead_tup=r[10], table_size_bytes=r[11],
        ) for r in rows]

    def _audit_rows(
        self, instance: str, now: float, snapshots: list[TableSnapshot],
    ) -> tuple[list[tuple], dict[tuple[str, str], int], dict[tuple[str, str], float], int]:
        """Diff one instance's snapshots against its cached previous pass
        -> (db_table_audit row tuples to WRITE, evaluated write_flags,
        their write_rates, tables seen). ACTIVITY-SPARSE: a row is written
        only when the table was active in the window (any of the seven
        counters moved) or its evaluated flag CHANGED (a clear is always
        recorded); a baseline/reset pass writes a census row per table
        (NULL rates). The flag map covers every evaluated primary table
        regardless of row-writing — transitions must fire even for a
        table whose writes just stopped."""
        prev = self._prev.get(instance, {})
        last_at = self._last_sample_at.get(instance)
        window_s: Optional[float] = (now - last_at) if last_at is not None else None
        evaluate_flags = instance == "primary" and window_s is not None

        rows: list[tuple] = []
        flags: dict[tuple[str, str], int] = {}
        rates: dict[tuple[str, str], float] = {}
        seen = 0
        for snap in snapshots:
            seen += 1
            key = (snap.table_schema, snap.table_name)
            old = prev.get(key)
            # window_s None (baseline) or a counter reset (any decrease)
            # -> unknowable deltas: NULL rates, flag not evaluated.
            known = (
                old is not None
                and window_s is not None
                and all(getattr(snap, f) >= getattr(old, f) for f in _COUNTER_FIELDS)
            )
            flag: Optional[int] = None
            if known and old is not None and window_s is not None:
                ins_d = snap.n_tup_ins - old.n_tup_ins
                upd_d = snap.n_tup_upd - old.n_tup_upd
                del_d = snap.n_tup_del - old.n_tup_del
                write_rate = (ins_d + upd_d + del_d) / window_s
                seq_d = snap.seq_scan - old.seq_scan
                idx_d = snap.idx_scan - old.idx_scan
                tup_read_d = snap.seq_tup_read - old.seq_tup_read
                tup_fetch_d = snap.idx_tup_fetch - old.idx_tup_fetch
                active = (
                    ins_d > 0 or upd_d > 0 or del_d > 0
                    or seq_d > 0 or idx_d > 0 or tup_read_d > 0 or tup_fetch_d > 0
                )
                if evaluate_flags:
                    flag = (
                        DB_WRITE_FLAG_CODE["red"]
                        if write_rate >= self._red_rps
                        else DB_WRITE_FLAG_CODE["amber"]
                        if write_rate >= self._amber_rps
                        else DB_WRITE_FLAG_CODE["ok"]
                    )
                    flags[key] = flag
                    rates[key] = write_rate
                    # an idle-but-cleared table still writes its clear row
                    active = active or flag != self._flags.get(key, DB_WRITE_FLAG_CODE["ok"])
                if active:
                    rows.append((
                        DB_INSTANCE_CODE[instance], snap.table_schema, snap.table_name,
                        round(window_s, 3),
                        round(write_rate, 3), ins_d, upd_d, del_d,
                        round((seq_d + idx_d) / window_s, 3),
                        seq_d, idx_d,
                        tup_read_d, tup_fetch_d,
                        snap.n_live_tup, snap.n_dead_tup, snap.table_size_bytes,
                        flag,
                    ))
            else:
                # baseline census row: full size/live-tup snapshot, no rates
                rows.append((
                    DB_INSTANCE_CODE[instance], snap.table_schema, snap.table_name,
                    None,
                    None, None, None, None,
                    None, None, None, None, None,
                    snap.n_live_tup, snap.n_dead_tup, snap.table_size_bytes,
                    None,
                ))
        return rows, flags, rates, seen

    # -- pass ---------------------------------------------------------------

    def run_pass(self, retain_days: int, alert_retain_days: int) -> None:
        """One pass: snapshot both instances, diff, append audit rows and
        flag transitions, trim retention. Never raises — a failed pass is
        logged and retried next interval."""
        try:
            now = time.monotonic()
            audit_rows: list[tuple] = []
            new_flags: dict[tuple[str, str], int] = {}
            new_rates: dict[tuple[str, str], float] = {}
            new_windows: dict[tuple[str, str], float] = {}
            seen_total = 0
            for instance in DB_INSTANCE_CODE:
                snapshots = self._fetch_snapshots(instance)
                if snapshots is None:
                    continue
                rows, flags, rates, seen = self._audit_rows(instance, now, snapshots)
                # the window this pass diffed against (before the state
                # advance below) — the evidence window for alert rows
                window_s = now - self._last_sample_at.get(instance, now)
                audit_rows.extend(rows)
                seen_total += seen
                if flags:
                    new_flags.update(flags)
                    new_rates.update(rates)
                    new_windows.update((k, window_s) for k in flags)
                # state advances only on a SUCCESSFUL sample — the next
                # pass's window then spans any outage gap truthfully.
                self._prev[instance] = {
                    (s.table_schema, s.table_name): s for s in snapshots
                }
                self._last_sample_at[instance] = now

            alerts = self._alert_rows_with_rates(new_flags, new_rates, new_windows)
            conn = get_db_connection()
            try:
                with conn.cursor() as cur:
                    if retain_days > 0:
                        cur.execute(_RETENTION_AUDIT_SQL, (retain_days,))
                    if alert_retain_days > 0:
                        cur.execute(_RETENTION_ALERT_SQL, (alert_retain_days,))
                    if audit_rows:
                        cur.executemany(_INSERT_AUDIT_SQL, audit_rows)
                    if alerts:
                        cur.executemany(_INSERT_ALERT_SQL, alerts)
            finally:
                conn.close()
            self._flags = new_flags
            logger.info("[ok] pass: %d audit rows across %d tables seen, %d alert(s)",
                        len(audit_rows), seen_total, len(alerts))
        except Exception as e:  # noqa: BLE001 — the sampler must outlive its target
            logger.error("[ALERT] pass failed (%s: %s) — rows for this pass lost; "
                         "will retry next interval", type(e).__name__, e)

    def _alert_rows_with_rates(
        self, flags: dict[tuple[str, str], int],
        rates: dict[tuple[str, str], float],
        windows: dict[tuple[str, str], float],
    ) -> list[tuple]:
        """Transitions for this pass's evaluated flags -> db_table_alert
        rows, each with the audited write_rate as evidence and its real
        log line emitted ([ALERT] on a raise, [ok] on a clear). The
        threshold recorded is the line the new state crossed: red on a red
        raise, amber on an amber raise or a clear."""
        label = {code: name for name, code in DB_WRITE_FLAG_CODE.items()}
        out: list[tuple] = []
        for key, flag in sorted(flags.items()):
            schema, table = key
            prev_flag = self._flags.get(key, DB_WRITE_FLAG_CODE["ok"])
            if flag == prev_flag:
                continue
            rate = rates.get(key, 0.0)
            window_s = windows.get(key, 0.0)
            threshold = (
                self._red_rps if flag == DB_WRITE_FLAG_CODE["red"]
                else self._amber_rps
            )
            full_name = f"{schema}.{table}"
            new_label = label[flag]
            if flag > prev_flag:  # a raise (ok→amber, ok→red, amber→red)
                message = (f"{full_name} write load {new_label}: "
                           f"{rate:.1f} rows/s over {window_s:.0f}s "
                           f">= {new_label} threshold {threshold:g} rows/s")
                log = logger.error if flag == DB_WRITE_FLAG_CODE["red"] else logger.warning
                log("[ALERT] %s", message)
            else:  # a downgrade or clear
                message = (f"{full_name} write load {new_label}: "
                           f"{rate:.1f} rows/s over {window_s:.0f}s "
                           f"< amber threshold {threshold:g} rows/s")
                logger.info("[ok] %s", message)
            out.append((schema, table, flag, rate, threshold, round(window_s, 3), message))
        return out


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m database.db_table_audit",
        description="Always-on per-table DB workload audit: read/write rates "
                    "per table from pg_stat_user_tables deltas -> "
                    "monitor.db_table_audit, write-heavy flag transitions -> "
                    "monitor.db_table_alert.",
    )
    parser.add_argument(
        "--interval", type=float, default=300.0,
        help="seconds between sample passes (default 300)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="one baseline pass and exit (no heartbeat registration; the "
             "first pass after a start has unknowable deltas, so rows "
             "carry NULL rates)",
    )
    parser.add_argument(
        "--retain-days", type=int, default=_DEFAULT_RETAIN_DAYS,
        help="purge audit rows older than this many days; 0 disables (default 30)",
    )
    parser.add_argument(
        "--alert-retain-days", type=int, default=_DEFAULT_ALERT_RETAIN_DAYS,
        help="purge alert rows older than this many days; 0 disables (default 90)",
    )
    args = parser.parse_args()

    sampler = DbTableAuditSampler(
        amber_rps=_env_float("DB_TABLE_WRITE_AMBER_RPS", AMBER_WRITE_RPS),
        red_rps=_env_float("DB_TABLE_WRITE_RED_RPS", RED_WRITE_RPS),
    )

    if args.once:
        sampler.run_pass(args.retain_days, args.alert_retain_days)
        return 0

    # Explicit scope: the service left MONITORED_SERVICES 2026-11 (a
    # monitor-area resident now), so the registry no longer supplies it.
    register_service(SERVICE, scope="database", sub_scope="stream")
    logger.info("db_table_audit sampler started (interval %.0fs, retain %dd, "
                "amber %.1f red %.1f rows/s)", args.interval, args.retain_days,
                sampler._amber_rps, sampler._red_rps)
    while True:
        started = time.monotonic()
        sampler.run_pass(args.retain_days, args.alert_retain_days)
        # Sleep the REMAINDER so pass time does not stretch the cadence.
        elapsed = time.monotonic() - started
        time.sleep(max(0.0, args.interval - elapsed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
