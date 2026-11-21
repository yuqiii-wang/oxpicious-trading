"""stream_watchdog — keep the downloads intraday stream always running.

The five ``downloads.stream.<exchange>.price`` pollers are nohup'd
``while True`` processes (formerly started by the retired
``scripts/stream_always.sh``) with no supervisor:
an unhandled exception outside a per-asset try/except, a WSL restart, a
Windows-update reboot or an OOM kill silently ends a stream until someone
notices missing intraday bars. This module is that supervisor — and a
service in its own right (it registers under
``_common.service_heartbeat.MONITORED_SERVICES`` like the pollers, so its
own death is detectable by ``database/monitoring.py --streams``).

2026-11 — THE RESIDENT MIGRATION COMPLETED AROUND IT: the pollers, the
seven cluster servers, the six infra daemons and THIS WATCHDOG itself
all moved into area servers' resident tables
(server_cluster/_common/resident_supervisor.py — desired state, crash
backoff, persisted manual stops, adoption, hung-runtime kills). The
scan below now covers exactly ONE service — ``server_cluster.monitor``
(the mutual pair: the monitor holds this watchdog's life as a resident
— the WSL boot hook starts it, the monitor's supervisor ADOPTS the
running instance and revives it when it dies; this watchdog keeps the
monitor alive, the one charge a server cannot hold itself). The
audit-orphan reconciliation pass is unchanged — every batch pipeline
still depends on it.

Loop (every ``--interval``, default 60s), for each service in
``MONITORED_SERVICES`` except itself:

  - no heartbeat row
      → a process for the module already running (a pre-heartbeat
        process, e.g. hand-started) is left alone with an ALERT — it must
        be restarted once to gain heartbeats; otherwise start it.
  - beat older than ``--stale-after`` (default 240s = 4 missed beats)
      → pid dead: mark the stop (if the process died WITHOUT an exit
        record — SIGKILL/OOM/power loss — record
        ``terminated without exit record``; a recorded exit keeps its own
        reason), reset ``monitor.service_heartbeat.live_parallel`` from
        pid+service-name liveness (a dead process cannot release its own
        parallel slot), and start a fresh process.
      → pid alive past ``--hung-after`` (default 1800s): SIGKILL it, mark
        the stop as a watchdog kill, and start a fresh one. A fresh beat
        proves the process RUNTIME is healthy (the heartbeat thread is
        independent of the poll loop); a stale beat with a live pid means
        the runtime itself is wedged (stuck I/O in the thread, D-state,
        ...) — not trustworthy.
      → pid alive below ``--hung-after``: ALERT only (give a slow startup
        or a brief DB outage time to recover).
  - STALE PROCESS (on a healthy, fresh beat): the heartbeat row carries
    the process life's ``code_fingerprint`` (a content hash over the
    service's source roots, stamped once at registration —
    _common/service_heartbeat.py); a mismatching FRESH disk hash means
    the sources moved under the running process, which keeps executing
    its startup-time code forever (observed live 2026-11-13: a daemon
    predating the provider plumbing silently ignored the fresh
    ``RunRequest.provider`` field). First sighting ALERTS only; when the
    same mismatch persists one scan (sources quiet), the process is
    SIGTERM'd (grace) / SIGKILL'd and restarted on the fresh sources —
    the same burst guard as any restart. Per-service opt-out:
    ``stale_process_check: false`` in the service's service_config/
    JSON.

  - Every restart is merged into ``monitor.service_audit``: the dead
    life's still-open audit row is closed with a ``watchdog restart: …``
    exit_reason (the trail the UI expands a stream row to), and the
    service is started detached with output appended to
    ``logs/<date>/<module>.log`` (the fleet's one-date-one-file
    convention).
    A burst guard (restart count over those merged rows) backs off when a
    service restarts repeatedly (crash loop) instead of hammering it
    every pass. A restarted service re-registers through
    _common/service_heartbeat.py, whose max_parallel guard auto-exits the
    newcomer if an alive same-service instance survived — no double-run.

Each cycle also runs one AUDIT ORPHAN reconciliation pass
(reconcile_audit_orphans): the repo's OTHER processes — batch builds /
downloads / analyses / llm runs, registered from DataPipeline.execute()
or autoregister() — have no supervisor, so their monitor.service_audit
rows left open by a SIGKILL / OOM kill / WSL shutdown are closed there
(pid-liveness per row; streams stay this loop's own concern) and their
heartbeat row's live_parallel rebuilt the same way.

Data freshness is deliberately NOT a restart trigger: pollers legitimately
sleep between fetch windows (up to a full day for the post-close
streamers), so only heartbeat absence — process death/wedging — is
actionable here. Intraday data gaps are the daily builds' concern.

Run:

  bash scripts/stream_watchdog.sh        # idempotent starter (WSL boot hook)
  python -u -m database.stream_watchdog  # direct, foreground
  python -m database.stream_watchdog --once        # single check pass
  python -m database.stream_watchdog --once --dry-run  # decisions only

DB down: no restarts (decisions need heartbeats) — the pass is logged as
failed and retried; the pollers keep running on their own.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _common.caution_log import record_service_error
from _common.db_commons import get_db_connection
from _common.log_setup import setup_logging
from _common.service_heartbeat import (
    HEARTBEAT_TABLE,
    MONITORED_SERVICES,
    STALE_AFTER_S,
    WATCHDOG_SERVICE,
    fingerprint_roots_for,
    pid_alive,
    pid_alive_for_service,
    register_service,
    source_fingerprint,
    stale_check_for,
)

logger = setup_logging("stream_watchdog")

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Services this watchdog supervises — everything in the registry except
# itself (its own liveness is check_streams' job, not its own). After the
# completed 2026-11 resident migration this is exactly ONE service: the
# monitor server (everything else lives in the area servers' resident
# tables, and this watchdog is the monitor area's own resident).
_SUPERVISED = tuple(s for s in MONITORED_SERVICES if s != WATCHDOG_SERVICE)

# Kill+restart a service whose beat is stale while its pid is STILL alive:
# the heartbeat thread is independent of the poll loop, so staleness with a
# live pid means the process runtime itself is wedged.
HUNG_AFTER_S = 1800.0

# Crash-loop guard: more than RESTART_BURST_LIMIT restarts within
# RESTART_BURST_WINDOW_S stops further restarts for RESTART_BACKOFF_S.
RESTART_BURST_LIMIT = 3
RESTART_BURST_WINDOW_S = 900
RESTART_BACKOFF_S = 600

# STALE-PROCESS restart — a service whose heartbeat's code_fingerprint
# (stamped once per life at registration) no longer matches a FRESH disk
# hash over its FINGERPRINT_ROOTS is executing startup-time code while
# the sources have moved on (observed live 2026-11-13: a deep-agents
# daemon predating the provider plumbing silently ignored the fresh
# RunRequest.provider field and ran requests on the wrong provider).
# SIGTERM first — a graceful unwind lets uvicorn close and the
# deep-agents daemon settle its in-flight run chains — bounded by this
# grace, then SIGKILL.
STALE_TERM_GRACE_S = 15.0

# Debounce for the stale-process restart: the mismatching DISK
# fingerprint must repeat on a later scan (sources quiet for one full
# interval) before the restart fires — a mid-edit storm then never
# kills the service under the developer's hands. service → disk hash.
_STALE_SEEN: dict[str, str] = {}

_STOP = threading.Event()

_HEARTBEATS_SQL = f"""
SELECT service,
       pid,
       EXTRACT(EPOCH FROM (now() - beat_at))::bigint AS beat_age_s,
       scope,
       sub_scope,
       stop_reason,
       code_fingerprint
FROM {HEARTBEAT_TABLE}
WHERE service = ANY(%s)
"""

# The watchdog restart trail lives on the dead life's service_audit row:
# every restart closes it with this exit_reason prefix (see _restart; the
# same prefix feeds the 24h restart counts in database/monitoring.py and
# the UI's recent-restarts list, via the service_audit_watchdog_restart_idx
# partial index).
RESTART_REASON_PREFIX = "watchdog restart: "

# Crash-loop guard: restarts recorded on the service's closed audit lives
# within RESTART_BURST_WINDOW_S (merged from monitor.stream_restart).
_BURST_SQL = """
SELECT count(*)
FROM monitor.service_audit
WHERE service = %s
  AND exit_reason LIKE %s
  AND ended_at > now() - make_interval(secs => %s)
"""

# Record the stop of a process that died without its own exit record
# (SIGKILL / OOM / power loss). Conditional: a recorded exit keeps its
# own reason.
_MARK_STOP_SQL = f"""
UPDATE {HEARTBEAT_TABLE}
SET stop_reason = %s,
    is_normal_exit = false,
    stopped_at = now()
WHERE service = %s
  AND stop_reason IS NULL
"""

# The restart trail, merged into service_audit: close the dead life's
# still-open audit row with the restart reason when the fresh process is
# spawned. ended_at is passed in by the caller (now() - the last beat's
# age: the beat was the last proof of life; "not running" passes now() —
# no beat to estimate from). pid excludes the freshly spawned process's
# own new audit life; rows already closed by the process's own exit
# record are kept as recorded.
_AUDIT_CLOSE_RESTART_SQL = """
UPDATE monitor.service_audit a
SET ended_at = GREATEST(a.started_at, %s),
    exit_reason = %s,
    is_normal_exit = false
WHERE a.service = %s
  AND a.ended_at IS NULL
  AND a.pid <> %s
"""

# The STALE-process path's audit override: SIGTERM is a graceful stop, so
# the dying process's own exit hook closes its audit life FIRST ('signal
# SIGTERM') and the close-above (ended_at IS NULL only) never touches it —
# the restart trail would read as a plain manual stop and the crash-loop
# burst count (keyed on the 'watchdog restart: ' prefix) would never see
# stale restarts. Re-point the just-closed row at the watchdog's reason:
# bounded to the dead pid, to rows closed since the stale decision began,
# and to the SIGTERM reason (never a differently-recorded exit).
_AUDIT_STALE_OVERRIDE_SQL = """
UPDATE monitor.service_audit
SET exit_reason = %s,
    is_normal_exit = false
WHERE service = %s
  AND pid = %s
  AND ended_at >= %s
  AND exit_reason = 'signal SIGTERM'
"""

# Open audit lives of ANY service (not just the supervised streams) with
# the service's last beat, for the orphan reconciliation pass: batch
# pipelines have no supervisor, so their runs killed without an exit
# record (SIGKILL / OOM / WSL shutdown) are closed here, per row, by pid
# liveness (a live sibling run's row must stay open).
_OPEN_AUDITS_SQL = f"""
SELECT a.id, a.service, a.pid, a.started_at, h.beat_at
FROM monitor.service_audit a
LEFT JOIN {HEARTBEAT_TABLE} h ON h.service = a.service
WHERE a.ended_at IS NULL
"""

_AUDIT_CLOSE_BY_ID_SQL = """
UPDATE monitor.service_audit
SET ended_at = %s,
    exit_reason = %s,
    is_normal_exit = false
WHERE id = %s
  AND ended_at IS NULL
"""

# live_parallel reset (monitor.service_heartbeat): a process that died
# without an exit record cannot release its own parallel slot, so every
# process-down detection rebuilds the counter from host liveness — an
# open audit life counts only while its pid still runs the service
# (pid + /proc cmdline; the watchdog's own host, where every pipeline
# runs).
_OPEN_LIVES_PIDS_SQL = """
SELECT DISTINCT pid
FROM monitor.service_audit
WHERE service = %s
  AND ended_at IS NULL
"""

_LIVE_RESET_SQL = f"""
UPDATE {HEARTBEAT_TABLE}
SET live_parallel = %s
WHERE service = %s
"""


def _reset_live_parallel(conn, service: str) -> None:
    """Rebuild live_parallel from the service's alive open audit lives
    (best effort — a failed reset leaves the counter stale until the
    service's next detected death)."""
    try:
        with conn.cursor() as cur:
            cur.execute(_OPEN_LIVES_PIDS_SQL, (service,))
            alive = [
                r[0] for r in cur.fetchall()
                if pid_alive_for_service(r[0], service)
            ]
            cur.execute(_LIVE_RESET_SQL, (len(alive), service))
    except Exception:  # noqa: BLE001
        logger.warning("%s: could not reset live_parallel", service)


def _find_running_module(module: str) -> int | None:
    """PID of a running ``python -m <module>`` process, if /proc is available.

    Used to ADOPT pre-heartbeat pollers (hand-started, or leftovers
    from the retired stream_always.sh era): they are alive but
    unmonitored until restarted once.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = [
                a.decode(errors="replace")
                for a in entry.joinpath("cmdline").read_bytes().split(b"\0")
                if a
            ]
        except OSError:
            continue
        if "-m" in argv and module in argv:
            return int(entry.name)
    return None


def _spawn(module: str) -> int:
    """Start ``python -u -m <module>`` detached, output appended to the
    day's service log ``logs/<date>/<module>.log`` — the SAME file the
    spawned process's _common/log_setup handler writes (one date one
    service one log file; both writers append)."""
    log_dir = _REPO_ROOT / "logs" / datetime.now().strftime("%Y-%m-%d")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_f = open(log_dir / f"{module}.log", "ab")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", module],
            stdout=log_f,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=_REPO_ROOT,
        )
    finally:
        log_f.close()
    return proc.pid


def _mark_stop(conn, service: str, reason: str) -> None:
    """Record a stop on the heartbeat row the process could not record
    itself (best effort — a failed mark only loses detail, never blocks
    the restart; the restart path itself closes the dead life's audit row
    via _AUDIT_CLOSE_RESTART_SQL, and the orphan pass closes rows by
    id)."""
    try:
        with conn.cursor() as cur:
            cur.execute(_MARK_STOP_SQL, (reason, service))
    except Exception:  # noqa: BLE001
        logger.warning("%s: could not mark stop record: %s", service, reason)


def _restart(
    conn, module: str, reason: str, dry_run: bool,
    ended_hint_s: float | None = None,
) -> bool:
    """(Re)start a service, with crash-loop guard. True = alert.

    The restart trail is MERGED into monitor.service_audit: the dead
    life's still-open audit row is closed with a ``watchdog restart: …``
    exit_reason (prev beat age is part of ``reason``; ended_hint_s — the
    last beat's age — estimates the death time), then the fresh process
    is spawned — it opens its own new audit life on registration.
    """
    if dry_run:
        logger.warning("[ALERT] %s: WOULD restart (%s)", module, reason)
        return True

    with conn.cursor() as cur:
        cur.execute(
            _BURST_SQL,
            (module, RESTART_REASON_PREFIX + "%", RESTART_BURST_WINDOW_S),
        )
        (n_recent,) = cur.fetchone()
    if n_recent >= RESTART_BURST_LIMIT:
        logger.error(
            "[ALERT] %s: %d restarts in the last %d min — crash loop suspected; "
            "backing off %d min (fix the cause in logs/ and restart manually).",
            module, n_recent, RESTART_BURST_WINDOW_S // 60, RESTART_BACKOFF_S // 60,
        )
        record_service_error(
            f"{module} crash loop: {n_recent} watchdog restarts in "
            f"{RESTART_BURST_WINDOW_S // 60} min — backed off",
            dedupe_key=f"watchdog-crash-loop:{module}",
            detail={"service": module, "recent_restarts": n_recent,
                    "window_s": RESTART_BURST_WINDOW_S},
            service=module,
        )
        return True

    pid = _spawn(module)
    ended_hint = (
        datetime.now() - timedelta(seconds=ended_hint_s)
        if ended_hint_s is not None
        else datetime.now()
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                _AUDIT_CLOSE_RESTART_SQL,
                (ended_hint, RESTART_REASON_PREFIX + reason, module, pid),
            )
    except Exception:  # noqa: BLE001 — the started service must not be undone
        logger.error(
            "%s: restarted as pid %d but the restart audit close failed",
            module, pid, exc_info=True,
        )
        return True
    logger.warning("[ALERT] %s: restarted as pid %d (%s)", module, pid, reason)
    # A restart means the poller DIED hard (SIGKILL / stale / burst) — the
    # dead process's own exit hooks never ran, so the caution trail would
    # miss it otherwise (monitor.caution_log, the Cautions tab; deduped
    # per service — a crash-looping poller bumps one open row).
    record_service_error(
        f"{module} died — watchdog restarted it as pid {pid} ({reason})",
        dedupe_key=f"watchdog-restart:{module}",
        detail={"service": module, "reason": reason, "new_pid": pid},
        service=module,
        severity="warning",
    )
    return True


# A one-shot pipeline life open this long is a hang (or a recycled pid
# holding the row): the longest observed legit runs are the csindex quote
# crawl (~9h) and the SSE pe_latest sweep (~3.2h). Alert-only — the
# watchdog never closes a row whose pid is still alive.
STALE_OPEN_RUN_AFTER_S = 24 * 3600.0


def _alert_stale_open_runs(
    lives: list[tuple], dead: list[tuple], service: str,
) -> None:
    """Caution the alive-but-stale open lives of one service (started
    > STALE_OPEN_RUN_AFTER_S ago): a wedged-but-running process (hung
    fetch loop) or a recycled pid keeps the audit row open forever and
    the dead-pid pass never sees it. The row itself stays open — a
    genuinely long run keeps its history correct; the caution (deduped
    'stale-open-run') bumps occurrence_count each cycle until it does."""
    now_utc = datetime.now(timezone.utc)
    stale = [
        r for r in lives
        if r not in dead
        and (now_utc - r[3]).total_seconds() > STALE_OPEN_RUN_AFTER_S
    ]
    if not stale:
        return
    oldest = min(r[3] for r in stale)
    hours = int((now_utc - oldest).total_seconds() // 3600)
    record_service_error(
        f"{service} run still open {hours}h after start (pid alive but no "
        f"exit record — hung run or recycled pid; watchdog did not close it)",
        dedupe_key="stale-open-run",
        detail={
            "service": service,
            "open_runs": len(stale),
            "oldest_started_at": oldest.isoformat(),
        },
        service=service,
        severity="warning",
    )
    logger.warning(
        "[ALERT] %s: %d audit run(s) still open after %dh — hung run or "
        "recycled pid.",
        service, len(stale), hours,
    )


def reconcile_audit_orphans() -> int:
    """Close run-audit rows of NON-supervised processes that died without
    an exit record. One pass per watchdog cycle; returns rows closed.

    The always-on streams are reconciled by check_once's own detection
    (restart + audit close); everything else — the batch builds /
    downloads / analyses / llm runs, registered via DataPipeline.execute()
    or autoregister() — has no supervisor, so rows left open by a SIGKILL
    / OOM kill / WSL shutdown are closed here, one row per PROCESS LIFE,
    skipping rows whose own pid is still alive (a concurrent run of the
    same module keeps its row open; pid recycling merely delays a close
    to a later pass). ended_at is estimated from the service's last
    heartbeat beat — the same estimate the stream path uses. When every
    open life of a service is dead the heartbeat row gets the stop mark
    too (a live sibling's next beat clears it again). Pids resolve only
    on the watchdog's own host — every pipeline runs in this same WSL
    distro.
    """
    conn = get_db_connection()
    closed = 0
    try:
        with conn.cursor() as cur:
            cur.execute(_OPEN_AUDITS_SQL)
            rows = cur.fetchall()
        by_service: dict[str, list[tuple]] = {}
        for r in rows:
            by_service.setdefault(r[1], []).append(r)
        for service, lives in by_service.items():
            if service in MONITORED_SERVICES:
                continue  # streams: check_once owns their reconciliation
            dead = [
                r for r in lives if not pid_alive(r[2])
            ]
            # Alive lives can be stale too (hung fetch loop, recycled pid)
            # — alert before the dead-only early-out below.
            _alert_stale_open_runs(lives, dead, service)
            if not dead:
                continue
            for audit_id, _svc, _pid, started_at, beat_at in dead:
                ended_at = max(started_at, beat_at) if beat_at else started_at
                with conn.cursor() as cur:
                    cur.execute(
                        _AUDIT_CLOSE_BY_ID_SQL,
                        (
                            ended_at,
                            "terminated without exit record (SIGKILL, OOM "
                            "or power loss)",
                            audit_id,
                        ),
                    )
            closed += len(dead)
            # Dead lives cannot release their own live_parallel slots —
            # rebuild the counter from the surviving open lives' pids.
            _reset_live_parallel(conn, service)
            if len(dead) == len(lives):
                # No live sibling: record the stop on the heartbeat row as
                # well (best effort; a beat from a restarted run clears it).
                _mark_stop(
                    conn, service,
                    "terminated without exit record (SIGKILL, OOM or "
                    "power loss)",
                )
            logger.warning(
                "[ALERT] %s: closed %d audit run(s) whose pid is dead — "
                "process died without an exit record.",
                service, len(dead),
            )
            # The dead lives' own exit hooks never ran (SIGKILL / OOM) —
            # surface the death as a caution too (monitor.caution_log,
            # the Cautions tab; deduped per service).
            record_service_error(
                f"{service} died without an exit record — {len(dead)} audit "
                f"run(s) closed by the watchdog (SIGKILL, OOM or power loss)",
                dedupe_key="terminated-no-exit-record",
                detail={"service": service, "closed_runs": len(dead)},
                service=service,
                severity="warning",
            )
    finally:
        conn.close()
    return closed


def _terminate_stale(pid: int) -> None:
    """Stop a LIVE stale process: SIGTERM first — the graceful unwind is
    the point (uvicorn closes its servers; the deep-agents daemon's
    in-flight runs settle their chains through the cancellation path) —
    bounded by STALE_TERM_GRACE_S, then SIGKILL whatever is left."""
    # PLANNED stop: mark before the signal so the dying process's
    # heartbeat records the stop WITHOUT the duplicate "stopped
    # abnormally" caution — this restart is intentional and the watchdog
    # logs it on its own caution trail.
    try:
        from _common.service_heartbeat import mark_planned_stop
        mark_planned_stop(pid, reason="stale-code restart")
    except Exception:  # noqa: BLE001 — best effort; the stop proceeds
        pass
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + STALE_TERM_GRACE_S
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.5)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _check_stale_code(
    conn, module: str, pid: int, row_fingerprint: str | None, dry_run: bool,
) -> int:
    """The stale-process gate, run on every HEALTHY beat (fresh beat +
    live pid): compare the heartbeat's per-life code fingerprint against
    a fresh disk hash of the service's source roots. Returns the alert
    count (0 or 1). NULL / "unknown" on either side is uncheckable —
    pre-column rows adopt out, a fingerprinting failure never restarts
    anything. A first mismatch only ALERTS; the restart fires when the
    same mismatching disk hash is still there one scan later (the
    debounce keeps a mid-edit storm from killing the service under the
    developer's hands)."""
    if not stale_check_for(module):
        return 0
    if not row_fingerprint or row_fingerprint == "unknown":
        return 0
    disk_fingerprint = source_fingerprint(fingerprint_roots_for(module))
    if disk_fingerprint == "unknown":
        return 0
    if disk_fingerprint == row_fingerprint:
        _STALE_SEEN.pop(module, None)
        return 0
    if _STALE_SEEN.get(module) != disk_fingerprint:
        _STALE_SEEN[module] = disk_fingerprint
        logger.warning(
            "[ALERT] %s: code changed under the running process (pid %d) — "
            "restarting next scan if the sources stay unchanged",
            module, pid,
        )
        return 1
    _STALE_SEEN.pop(module, None)
    if dry_run:
        logger.warning(
            "[ALERT] %s: WOULD restart (stale process — pid %d runs "
            "startup-time code, the sources moved on)", module, pid,
        )
        return 1
    logger.warning(
        "[ALERT] %s: stale process (pid %d) — code changed since start and "
        "stayed changed for one scan; terminating (SIGTERM, %.0fs grace) "
        "and restarting on the fresh sources.",
        module, pid, STALE_TERM_GRACE_S,
    )
    reason = (
        f"watchdog restart: stale process (pid {pid} ran startup-time "
        "code) — restarted on the fresh sources"
    )
    stale_since = datetime.now(timezone.utc)
    _terminate_stale(pid)
    # The SIGTERM'd process closed its own audit life ('signal SIGTERM')
    # before dying — re-point it at the watchdog's reason (the restart
    # trail AND the crash-loop burst count read this).
    with conn.cursor() as cur:
        cur.execute(
            _AUDIT_STALE_OVERRIDE_SQL,
            (reason, module, pid, stale_since),
        )
    _mark_stop(
        conn, module,
        "stopped by stream_watchdog (stale process — code changed since "
        "process start)",
    )
    _reset_live_parallel(conn, module)
    return _restart(
        conn, module,
        reason[len(RESTART_REASON_PREFIX):],
        dry_run,
        ended_hint_s=0,
    )


def check_once(
    stale_after_s: float,
    hung_after_s: float,
    dry_run: bool,
    services: tuple[str, ...] = _SUPERVISED,
) -> int:
    """One supervision pass (default: all supervised services; the
    ``services`` override is for tests). Returns alert count."""
    alerts = 0
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_HEARTBEATS_SQL, (list(services),))
            beats = {
                row[0]: row[1:]  # (pid, beat_age_s, scope, sub_scope,
                #                 stop_reason, code_fingerprint)
                for row in cur.fetchall()
            }

        for module in services:
            row = beats.get(module)
            if row is None:
                running_pid = _find_running_module(module)
                if running_pid is not None:
                    logger.warning(
                        "[ALERT] %s: running (pid %d) but no heartbeat row — "
                        "pre-heartbeat build? Left untouched; restart it once "
                        "to gain heartbeats.",
                        module, running_pid,
                    )
                    alerts += 1
                else:
                    alerts += _restart(
                        conn, module, "no heartbeat row (not running)",
                        dry_run,
                    )
                continue

            pid, beat_age_s, _scope, _sub_scope, stop_reason, row_fingerprint = row
            # Liveness IS pid + service name (pid_alive_for_service), never
            # bare /proc existence: after a distro reboot the heartbeat
            # row's pid routinely points at an UNRELATED recycled process,
            # and a bare alive verdict there turns the stale-beat pass into
            # a "wedged runtime" SIGKILL of an innocent process (observed
            # live 2026-11-10: a freshly-spawned poller was killed seconds
            # after the post-reboot fleet restart).
            if beat_age_s <= stale_after_s and pid_alive_for_service(pid, module):
                logger.info("[ok] %s: beat %.0fs ago (pid %d)", module, beat_age_s, pid)
                # Alive and beating is NOT the whole health story: the
                # process may be executing startup-time code while the
                # sources have moved on (the heartbeat's per-life
                # code_fingerprint vs a fresh disk hash).
                alerts += _check_stale_code(
                    conn, module, pid, row_fingerprint, dry_run,
                )
                continue

            if not pid_alive_for_service(pid, module):
                # A dead (or recycled) pid is dead regardless of beat
                # freshness — a beat cannot be newer than the process's
                # death. Restart now instead of waiting out the stale
                # window. The no-response check IS pid + service name: a
                # dead process cannot release its own live_parallel slot,
                # so reset the counter from host liveness before the fresh
                # process claims one.
                if stop_reason is None and not dry_run:
                    _mark_stop(
                        conn, module,
                        "terminated without exit record (SIGKILL, OOM or "
                        "power loss)",
                    )
                if not dry_run:
                    _reset_live_parallel(conn, module)
                alerts += _restart(
                    conn, module,
                    f"pid {pid} dead (beat {beat_age_s}s old)", dry_run,
                    ended_hint_s=beat_age_s,
                )
            elif beat_age_s > hung_after_s:
                logger.warning(
                    "[ALERT] %s: beat stale %.0fs and pid %d still alive — "
                    "wedged runtime; killing and restarting.",
                    module, beat_age_s, pid,
                )
                if not dry_run:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                    _mark_stop(conn, module, "killed by stream_watchdog (wedged runtime)")
                    _reset_live_parallel(conn, module)
                alerts += _restart(
                    conn, module,
                    f"heartbeat stale {beat_age_s}s with live pid {pid} — "
                    "wedged, killed",
                    dry_run,
                    ended_hint_s=beat_age_s,
                )
            else:
                logger.warning(
                    "[ALERT] %s: beat stale %.0fs but pid %d alive (below the "
                    "%.0fs hung threshold) — waiting before intervening.",
                    module, beat_age_s, pid, hung_after_s,
                )
                alerts += 1
    finally:
        conn.close()
    return alerts


def run(
    interval_s: float, stale_after_s: float, hung_after_s: float,
    once: bool, dry_run: bool,
) -> int:
    logger.info(
        "stream_watchdog started (interval=%.0fs stale_after=%.0fs "
        "hung_after=%.0fs once=%s dry_run=%s services=%d)",
        interval_s, stale_after_s, hung_after_s, once, dry_run,
        len(_SUPERVISED),
    )
    total_alerts = 0
    while not _STOP.is_set():
        try:
            total_alerts += check_once(stale_after_s, hung_after_s, dry_run)
        except Exception as e:  # noqa: BLE001 — DB down etc.; retry next pass
            logger.error(
                "check pass failed (DB down?): %s: %s", type(e).__name__, e,
            )
            total_alerts += 1
        if not dry_run:
            try:
                total_alerts += 1 if reconcile_audit_orphans() else 0
            except Exception as e:  # noqa: BLE001 — retry next pass
                logger.error(
                    "audit reconciliation failed: %s: %s",
                    type(e).__name__, e,
                )
                total_alerts += 1
        if once:
            break
        _STOP.wait(interval_s)
    return 1 if total_alerts else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m database.stream_watchdog",
        description="Supervisor that keeps the downloads intraday stream "
                    "services running (heartbeat-based restarts).",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="run a single check pass and exit (exit 1 = alerts)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print restart decisions without starting/killing anything",
    )
    parser.add_argument(
        "--interval", type=float, default=60.0,
        help="check pass cadence in seconds (default 60)",
    )
    parser.add_argument(
        "--stale-after", type=float, default=STALE_AFTER_S,
        help=f"heartbeat age over which a service counts as dead/wedged "
             f"(default {STALE_AFTER_S:.0f}s)",
    )
    parser.add_argument(
        "--hung-after", type=float, default=HUNG_AFTER_S,
        help=f"heartbeat age over which a STILL-ALIVE service is killed and "
             f"restarted (default {HUNG_AFTER_S:.0f}s)",
    )
    args = parser.parse_args()

    # The heartbeat module installs the SIGTERM/excepthook hooks: a SIGTERM
    # raises SystemExit here, atexit records the stop, and the exit code is
    # 128+15.
    register_service(WATCHDOG_SERVICE)

    try:
        return run(
            interval_s=args.interval,
            stale_after_s=args.stale_after,
            hung_after_s=args.hung_after,
            once=args.once,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        logger.info("Interrupted by user; exiting.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
