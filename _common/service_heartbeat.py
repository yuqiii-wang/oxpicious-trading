"""service_heartbeat — unified service name + heartbeat for ALL pipelines.

EVERY Python process in this repo registers itself once, near its entry
point (after argparse, before the work):

    from _common.service_heartbeat import autoregister
    autoregister()                     # derives the service name from the
                                       # __main__ entry file, e.g.
                                       # downloads/macro/gov/news/__main__.py
                                       # → "downloads.macro.gov.news"

DataPipeline subclasses (builds.* / analysis.* / strategy.*) get this for
free: ``DataPipeline.execute()`` registers by the entry class's module.
The always-on stream pollers register explicitly (registry-known names):

    from _common.service_heartbeat import register_service
    register_service("downloads.stream.sse.price")          # registry-known

Registration starts a daemon thread that upserts the service's row in
``monitor.service_heartbeat`` every ``HEARTBEAT_INTERVAL_S`` (unique service
name, scope/sub_scope, pid, started_at, beat_at) and opens one row in
``monitor.service_audit`` (the append-only per-run history). The heartbeat
row IS the process's unique service name — the DB-persisted counterpart of
the UI builds' in-memory process-id-tag registry
(data_viz/api/services/py-runner.service.ts), which only knows processes
the Express API spawned. A fresh beat proves the process RUNTIME is
healthy; it says nothing about loop progress (batch runs and sleeping
pollers legitimately go quiet between windows — data freshness lives in
the data tables). For batch pipelines the heartbeat row is the LAST-RUN
state (a stopped row with its exit reason between runs) and the audit
table is the run history; for the always-on streams it is liveness
(watched by the stream watchdog). Concurrent runs of the SAME module share
the one heartbeat row (each beat carries its own pid; the row flip-flops
until the last run exits) while each run keeps its own audit row — up to
the service's ``max_parallel`` (static JSON config —
database/sql/monitoring/static/service_config/<dotted.service.path>.json,
tree-root default.json fallback, default 1):
registration atomically CLAIMS a slot
(monitor.service_heartbeat.live_parallel + 1 while below the cap) and a
process past the cap AUTO-EXITS instead of
starting, so a duplicate start (manual double-launch, cron overlap, a
watchdog restart racing a manual run) refuses itself. A recorded exit
releases the slot; a process that dies without one cannot, so the stream
watchdog RESETS live_parallel from host pid liveness (pid + cmdline must
still run the service) whenever it detects a process down.

On process exit the module records HOW the process stopped, in
stop_reason / is_normal_exit / stopped_at (NULL while running):

  - clean interpreter exit (app-handled Ctrl-C, ``--once`` completion,
    batch run finished)                       → is_normal_exit = true
  - SIGTERM (external stop, incl. watchdog grace) → is_normal_exit = false
  - unhandled exception                        → is_normal_exit = false
  - death WITHOUT any record (SIGKILL, OOM, power loss) → no row change;
    database/stream_watchdog.py detects it (stale beat + dead pid with
    stop_reason IS NULL) and records it, then restarts the service.

Cross-SERVICE parallelism is static JSON config too
(included_parallel_services / excluded_parallel_services in the same
service_config/ files — entries are rooted dotted prefixes: a scope
"downloads", a sub-scope path "downloads.stock", or the full service
name "downloads.stock.sse.archive"; specificity = segment count, so a
name beats a sub-scope beats a scope on conflict, and a same-specificity
tie resolves to excluded): registration checks both directions — the
starting service's lists against every live service and every live
service's lists against the starter (a live service = a heartbeat row
with no stop record and a fresh beat) — and AUTO-EXITS on the first
conflict, releasing its slot. An empty included list means
unrestricted; a non-empty one is a whitelist (unlisted services may not
run in parallel). The UI's run dialog previews the same check (the
DB-unreachable case skips it — a monitoring outage must not stop
pipelines).

The thread must never take the service down: a failed beat is logged to
stderr (captured in the service's nohup log) and retried, rebuilding the
DB connection after any error.

IMPORT ORDER — this module imports _common.db_commons, which transitively
imports pandas (via _batched_copy). Import it LAZILY (function-local) from
any module that must install the cudf.pandas hook before pandas' first
import; every helper below is safe to call once bootstrap_runtime() has
run.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

from _common.db_commons import get_db_connection
from _common.labels import PROGRESS_PHASE_CODE, PROGRESS_STATE_CODE, \
    SERVICE_COMMAND_CODE

# ---------------------------------------------------------------------------
# Registry — unique service name -> (scope, sub_scope)
# ---------------------------------------------------------------------------
# The stream watchdog's own scan charges — after the 2026-11 resident
# migration, exactly ONE (plus the watchdog itself). scope is the
# top-level pipeline area (downloads / builds / analysis / strategy / live /
# database); sub_scope the level below it. Batch pipelines and the resident
# services register ad-hoc / with explicit scope args instead of being
# listed here.
WATCHDOG_SERVICE = "database.stream_watchdog"

MONITORED_SERVICES: dict[str, tuple[str, str | None]] = {
    # The 2026-11 RESIDENT MIGRATION, COMPLETE — every always-on process
    # but the boot-hook root is now an area server's resident
    # (server_cluster/_common/resident_supervisor.py — desired state,
    # crash backoff, persisted manual stops, adoption, hung-runtime
    # kills; the central watchdog unconditionally revives, the area
    # supervisor does not). The pollers went to the downloader server's
    # resident table, the seven other cluster servers to the monitor's,
    # and the six infra daemons (db_health / db_table_audit / sql_audit
    # / mcp / sandbox / deep_agents server) plus the WATCHDOG ITSELF
    # joined the monitor's table: the mutual pair — the monitor holds
    # the watchdog's life as a resident, the watchdog keeps the monitor
    # here as its ONE scan charge (a server cannot supervise itself,
    # and the chain still needs a root: WSL boot → stream_watchdog →
    # monitor → residents).
    "server_cluster.monitor": ("server_cluster", "monitor"),
    WATCHDOG_SERVICE: ("database", "monitoring"),
}


def always_on_roster() -> list[str]:
    """Every service that should ALWAYS have a heartbeat: the watchdog's
    own scan charges (the registry above) plus every area server's
    residents (server_cluster/_common/config.py — the completed 2026-11
    resident migration moved the pollers, the seven cluster servers, the
    six infra daemons and the watchdog itself into resident tables, so
    the registry alone no longer names the fleet). The one source of
    truth for the liveness views that must cover the whole always-on
    set (database.monitoring's stream check, the monitor area's
    always-on aggregation). Lazy config import: that module pulls
    pydantic only, but this module must stay importable for services
    that never touch the cluster."""
    from server_cluster._common.config import ClusterConfig

    names = set(MONITORED_SERVICES)
    for cfg in ClusterConfig.load().areas.values():
        names.update(r.service for r in cfg.residents)
    return sorted(names)

# Per-service SOURCE-FINGERPRINT roots — the repo-relative directories
# whose *.py files define the code a monitored service runs. The process
# stamps their hash on its heartbeat at registration (one fingerprint per
# LIFE — it never recomputes), and database/stream_watchdog.py recomputes
# it from disk each scan: a mismatch means the sources moved under a
# running process (the stale-process incident of 2026-11-13 — a daemon
# started Nov 12 silently ignored the fresh RunRequest.provider field and
# ran requests on the wrong provider until manually restarted). Default
# roots for an unlisted service: its TOP-LEVEL package directory + the
# shared _common tree (see fingerprint_roots_for).
FINGERPRINT_ROOTS: dict[str, tuple[str, ...]] = {
    "server_cluster.monitor": ("server_cluster", "_common"),
    "database.db_health": ("database", "_common"),
    "database.db_table_audit": ("database", "_common"),
    "database.sql_audit": ("database", "_common"),
    "llm_agents.mcp": ("llm_agents", "_common"),
    "llm_agents.sandbox": ("llm_agents", "_common"),
    # The deep-agents daemon ALSO imports the test package at runtime —
    # the ``[this is test]`` self-test walks test/dummy_langgraph's
    # bodies — so its fingerprint covers the test tree too (an edit
    # there must recycle the daemon or the in-process module cache
    # keeps serving the old walk code; observed 2026-11-14 f).
    "llm_agents.deep_agents.server": ("llm_agents", "_common", "test"),
    WATCHDOG_SERVICE: ("database", "_common"),
}

# A service counts as stale — dead or wedged — once its beat is this old.
# 4 missed beats at the default 60s cadence; generous enough to ride out a
# DB hiccup or a slow NTP step, short enough to restart a dead poller
# within ~4 minutes.
STALE_AFTER_S = 240.0

HEARTBEAT_TABLE = "monitor.service_heartbeat"
HEARTBEAT_INTERVAL_S = 60.0

# A running beat also CLEARs the stop columns: a live process is, by
# definition, not stopped (this clears stale stop info from a previous
# life after restart). started_at is NOT touched on conflict: the run's
# start is stamped once by _CLAIM_SQL when the new life takes over the
# row — updating it here would make started_at tick with every beat and
# "Last run started" drift forward while the process runs. The same
# per-life logic applies to code_fingerprint: the process computes it
# ONCE at registration (source_fingerprint over its FINGERPRINT_ROOTS)
# and re-writes the SAME value every beat — database/stream_watchdog.py
# recomputes it from DISK per scan, so a drifting value under a live
# beat is the stale-process signal.
_BEAT_SQL = f"""
INSERT INTO {HEARTBEAT_TABLE}
    (service, scope, sub_scope, pid, started_at, beat_at,
     stop_reason, is_normal_exit, stopped_at, code_fingerprint)
VALUES (%s, %s, %s, %s, now(), now(), NULL, NULL, NULL, %s)
ON CONFLICT (service) DO UPDATE
SET scope = EXCLUDED.scope,
    sub_scope = EXCLUDED.sub_scope,
    pid = EXCLUDED.pid,
    beat_at = now(),
    stop_reason = NULL,
    is_normal_exit = NULL,
    stopped_at = NULL,
    code_fingerprint = EXCLUDED.code_fingerprint
"""

# The stop write is an UPSERT too: a process that dies before its first
# beat (fast crash) still leaves a record.
_STOP_SQL = f"""
INSERT INTO {HEARTBEAT_TABLE}
    (service, scope, sub_scope, pid, started_at, beat_at,
     stop_reason, is_normal_exit, stopped_at)
VALUES (%s, %s, %s, %s, now(), now(), %s, %s, now())
ON CONFLICT (service) DO UPDATE
SET stop_reason = EXCLUDED.stop_reason,
    is_normal_exit = EXCLUDED.is_normal_exit,
    stopped_at = now()
"""


# Service audit (monitor.service_audit): one INSERT per registered process
# life, closed with ended_at/exit_reason/is_normal_exit on exit.
_AUDIT_INSERT_SQL = """
INSERT INTO monitor.service_audit (service, scope, sub_scope, pid, started_at)
VALUES (%s, %s, %s, %s, now())
RETURNING id
"""

_AUDIT_CLOSE_SQL = """
UPDATE monitor.service_audit
SET ended_at = now(),
    exit_reason = %s,
    is_normal_exit = %s
WHERE id = %s
"""


def beat_once(
    service: str, scope: str | None = None, sub_scope: str | None = None,
    pid: int | None = None, code_fingerprint: str | None = None,
) -> None:
    """One synchronous RUNNING-beat upsert (tests; the watchdog thread
    covers its own service in production)."""
    reg_scope, reg_sub = MONITORED_SERVICES.get(service, (scope, sub_scope))
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                _BEAT_SQL,
                (
                    service,
                    reg_scope or "unknown",
                    reg_sub,
                    pid if pid is not None else os.getpid(),
                    code_fingerprint,
                ),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Host pid liveness — shared by the stream watchdog and the max_parallel
# registration guard below
# ---------------------------------------------------------------------------


def pid_alive(pid: int) -> bool:
    """/proc stat state liveness — NOT os.kill(pid, 0): a signal-0 probe
    succeeds on a ZOMBIE (exited but unreaped; e.g. the stream watchdog's
    own spawned children stay unreaped until its next spawn), which would
    keep a dead process looking alive. A zombie cannot beat — it counts as
    dead here. Hosts without /proc (a Windows dev box) report every pid
    dead; all real pipelines run in the repo's WSL distro."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_bytes()
    except (ProcessLookupError, FileNotFoundError):
        return False
    except OSError:
        # Unreadable /proc entry (permission?) — fall back to the signal
        # probe; a pid we can neither read nor signal is dead for us.
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    return stat.rsplit(b")", 1)[-1].split()[0] != b"Z"


def pid_alive_for_service(pid: int, service: str) -> bool:
    """``pid_alive`` narrowed to the service: the /proc cmdline must run
    ``service`` as a ``-m`` module, so a recycled pid now executing
    something unrelated cannot keep a stale audit life looking alive."""
    if not pid_alive(pid):
        return False
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return True  # unreadable cmdline: keep pid_alive's verdict
    return service.encode() in argv


# ---------------------------------------------------------------------------
# Module-path registration — the batch/analysis/live/llm pipelines
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]


def fingerprint_roots_for(service: str) -> tuple[str, ...]:
    """The repo-relative source roots whose *.py files fingerprint a
    service's running code — the FINGERPRINT_ROOTS entry when listed,
    else the module's TOP-LEVEL package directory + the shared _common
    tree (``downloads.stream.szse.price`` → ("downloads", "_common"))."""
    listed = FINGERPRINT_ROOTS.get(service)
    if listed is not None:
        return listed
    top = service.split(".")[0] if service else ""
    return (top, "_common") if top else ("_common",)


def source_fingerprint(roots: tuple[str, ...]) -> str:
    """A content hash over the *.py sources under ``roots`` — the code
    fingerprint a heartbeat life stamps at registration and the stream
    watchdog recomputes from disk per scan. md5 over the files' sorted
    relative paths + sizes + contents: content-based on purpose, since
    this repo is edited on Windows and run in WSL and the cross-boundary
    mtime check is already known unreliable (the __pycache__ convention
    exists for it). Fail-soft: any walk/read problem returns "unknown"
    (the staleness check treats it as uncheckable — a fingerprinting
    error must never restart a healthy service)."""
    try:
        digest = hashlib.md5()
        for root in roots:
            base = _REPO_ROOT / root
            if not base.is_dir():
                continue  # a missing root contributes nothing (stable)
            for path in sorted(base.rglob("*.py")):
                rel = path.relative_to(_REPO_ROOT).as_posix()
                data = path.read_bytes()
                digest.update(rel.encode("utf-8"))
                digest.update(b"\0")
                digest.update(str(len(data)).encode("ascii"))
                digest.update(b"\0")
                digest.update(data)
        return digest.hexdigest()
    except Exception:  # noqa: BLE001 — fail-soft to uncheckable
        return "unknown"


_SERVICE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def service_from_module_path(module: str) -> tuple[str, str, str | None]:
    """Dotted module path → (service, scope, sub_scope).

    ``builds.stock.__main__`` → ("builds.stock", "builds", "stock");
    ``database.stream_watchdog`` → ("database.stream_watchdog", "database",
                                 "monitoring"). scope is the top-level
    pipeline area (first segment), sub_scope the second — the same
    taxonomy the MONITORED_SERVICES registry uses.

    Segments must fit the static service_config tree's dotted-prefix
    charset ([A-Za-z0-9_-] — the same contract
    data_viz/api/services/static-config.service.ts enforces): a name
    outside it raises ValueError so autoregister() degrades to
    unmonitored instead of writing a row the monitor API cannot map
    back to a config (a stdin-run script's ``__main__.__file__`` is the
    literal ``<stdin>`` — registered as-is 2026-11-07, it 500'd
    GET /api/monitor/stream until the row was purged).
    """
    parts = module.split(".")
    if parts and parts[-1] == "__main__":
        parts = parts[:-1]
    if not parts or not all(parts) or not all(
        _SERVICE_SEGMENT_RE.fullmatch(p) for p in parts
    ):
        raise ValueError(f"cannot derive a service name from {module!r}")
    service = ".".join(parts)
    scope = parts[0]
    sub_scope = parts[1] if len(parts) > 1 else None
    return service, scope, sub_scope


def register_pipeline(
    module: str, interval_s: float = HEARTBEAT_INTERVAL_S,
) -> HeartbeatThread:
    """Register by dotted module path, deriving scope/sub_scope from its
    segments (service_from_module_path). The DataPipeline.execute() hook
    and autoregister() both land here."""
    service, scope, sub = service_from_module_path(module)
    return register_service(service, scope=scope, sub_scope=sub,
                            interval_s=interval_s)


def autoregister(
    interval_s: float = HEARTBEAT_INTERVAL_S,
) -> HeartbeatThread | None:
    """Register the running process by its ENTRY file, derived from
    ``sys.modules["__main__"].__file__`` relative to the repo root
    (``downloads/macro/gov/news/__main__.py`` → downloads.macro.gov.news;
    ``database/stream_watchdog.py`` → database.stream_watchdog).

    Best effort — a registration failure (no DB, odd layout) warns on
    stderr and returns None so the pipeline runs unmonitored rather than
    dying. Call from the ``__main__`` block / after argparse.
    """
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    if not main_file:
        print(
            "[service_heartbeat] autoregister: no __main__.__file__ — "
            "not registering",
            file=sys.stderr, flush=True,
        )
        return None
    try:
        rel = Path(main_file).resolve().relative_to(_REPO_ROOT)
        parts = [p for p in rel.with_suffix("").parts]
        if parts and parts[-1] == "__main__":
            parts = parts[:-1]
        if not parts:
            raise ValueError(f"entry file {main_file} maps to no service")
        return register_pipeline(".".join(parts), interval_s=interval_s)
    except Exception as e:  # noqa: BLE001 — never kill the pipeline
        print(
            f"[service_heartbeat] autoregister failed ({e}); running "
            "unmonitored",
            file=sys.stderr, flush=True,
        )
        return None


class HeartbeatThread(threading.Thread):
    """Daemon thread upserting one service's heartbeat row forever.

    The loop runs in ``_MAILBOX_POLL_S`` (5s) slices: every 12th tick
    carries the 60s beat (the cadence unchanged), EVERY tick polls the
    command mailbox (Phase 4, 2026-11-21 — claimed commands dispatch
    into the service's in-process state for the work loop's
    checkpoints). Failures are rate-limited to one stderr line per 30
    minutes — a DB outage must not flood the service's nohup log; the
    watchdog notices the missing beats on its own.
    """

    def __init__(
        self, service: str, scope: str, sub_scope: str | None,
        interval_s: float = HEARTBEAT_INTERVAL_S,
        code_fingerprint: str | None = None,
    ):
        super().__init__(name=f"heartbeat-{service}", daemon=True)
        self._service = service
        self._scope = scope
        self._sub_scope = sub_scope
        self._interval_s = interval_s
        self._code_fingerprint = code_fingerprint
        self._halt = threading.Event()
        self._last_warn = 0.0

    def _warn(self, msg: str) -> None:
        now = time.monotonic()
        if now - self._last_warn < 1800.0:
            return
        self._last_warn = now
        # stderr lands in the service's nohup log next to its own logging.
        print(
            f"[service_heartbeat] {self._service}: {msg}",
            file=sys.stderr,
            flush=True,
        )

    def run(self) -> None:
        conn = None
        ticks = 0
        beat_every = max(1, round(self._interval_s / _MAILBOX_POLL_S))
        while not self._halt.is_set():
            if conn is None:
                try:
                    conn = get_db_connection()
                except Exception as e:  # noqa: BLE001 — never kill the service
                    self._warn(f"connect failed, retrying next tick: {e}")
                    self._halt.wait(_MAILBOX_POLL_S)
                    continue
            if ticks % beat_every == 0:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            _BEAT_SQL,
                            (self._service, self._scope, self._sub_scope,
                             os.getpid(), self._code_fingerprint),
                        )
                except Exception as e:  # noqa: BLE001
                    self._warn(f"beat failed, reconnecting: {e}")
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
                    conn = None
                    ticks += 1
                    self._halt.wait(_MAILBOX_POLL_S)
                    continue
            try:
                self._poll_mailbox(conn)
            except Exception as e:  # noqa: BLE001 — no commands flow
                self._warn(f"mailbox poll failed, reconnecting: {e}")
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = None
            ticks += 1
            self._halt.wait(_MAILBOX_POLL_S)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _poll_mailbox(self, conn) -> None:
        """Claim (consume atomically) every pending command for this
        service and dispatch into its in-process state. Bounded per
        tick; a DB problem raises to the caller's reconnect."""
        st = _command_states.get(self._service)
        if st is None:
            return
        for _ in range(16):
            with conn.cursor() as cur:
                cur.execute(
                    # TWO placeholders: the claim's consumer pid + the
                    # service name (the sub-select's ordering carries
                    # delivery order).
                    _CLAIM_COMMAND_SQL,
                    (os.getpid(), self._service),
                )
                row = cur.fetchone()
            if row is None:
                return
            _dispatch_command(st, row[1], row[2])

    def stop(self) -> None:
        self._halt.set()


# ---------------------------------------------------------------------------
# Exit recording — stop_reason / is_normal_exit / stopped_at
# ---------------------------------------------------------------------------

# A max_parallel / parallel-services refusal exits with THIS code (not 1):
# the orchestration tier runner treats it as "already running elsewhere —
# deduped, counted OK" instead of a step failure (a re-started tier racing
# its own surviving children, 2026-11-20). Any other exit code stays a
# failure for every consumer.
EXIT_ALREADY_RUNNING = 10

# PLANNED-STOP MARKERS — a supervisor (resident_supervisor / registry stop /
# stream watchdog stale-restart) SIGTERMs a process ON PURPOSE: it writes
# <locks>/planned-stop-<pid> right before the signal, and the dying
# process's SIGTERM handler consumes it. A planned stop still records its
# normal stop row (reason stays 'signal SIGTERM' — the stream watchdog's
# audit-override SQL matches that string), but does NOT raise the
# per-service "stopped abnormally" caution: the SUPERVISOR already logs the
# restart on its own caution trail, and the duplicate error-severity row
# per restart was pure noise (2026-11-20: 15+ open rows from one code-change
# restart wave). Cross-process by design; best effort throughout.
_PLANNED_STOP_MAX_AGE_S = 120.0  # a marker older than this is stale (pid reuse)


def mark_planned_stop(pid: int, reason: str = "supervisor restart") -> None:
    """Flag ``pid``'s upcoming SIGTERM as PLANNED (see above). Best effort —
    a failed marker write only means the stop logs like an abnormal one."""
    try:
        import tempfile

        marker_dir = Path(tempfile.gettempdir()) / "oxpicious-trading-locks"
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / f"planned-stop-{pid}.marker").write_text(
            f"{reason}\n{time.time()}", encoding="utf-8")
    except OSError:
        pass


def _consume_planned_stop(pid: int) -> str | None:
    """The planned-stop reason for THIS pid, if a fresh marker exists.

    Consumes the marker (unlink) either way; a marker older than
    ``_PLANNED_STOP_MAX_AGE_S`` is ignored — the stop it announced never
    happened and the pid may have been reused.
    """
    import tempfile

    marker = (Path(tempfile.gettempdir()) / "oxpicious-trading-locks"
              / f"planned-stop-{pid}.marker")
    try:
        rows = marker.read_text(encoding="utf-8").splitlines()
        stamped = float(rows[1]) if len(rows) > 1 else 0.0
    except (OSError, ValueError):
        return None
    finally:
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass
    if time.time() - stamped > _PLANNED_STOP_MAX_AGE_S:
        return None
    return rows[0] if rows else "planned stop"


_threads: dict[str, HeartbeatThread] = {}
_exit_info: dict[str, tuple[str, bool]] = {}
_audit_ids: dict[str, int] = {}
_slots_claimed: dict[str, bool] = {}  # service → live_parallel slot taken
_planned_stop_reason: str | None = None  # set by _on_sigterm (this process)
_prev_sigterm: signal._HANDLER_TYPE | None = None
_prev_excepthook = None
_hooks_installed = False
_hooks_lock = threading.Lock()


def current_service() -> str | None:
    """The service name THIS process registered under (first registration
    wins — virtually every process registers exactly one), or None when
    nothing registered. A read-only peek other modules can use to
    attribute their rows to the running process (e.g.
    _common/external_resource_audit.py) without importing this module
    eagerly: call it through sys.modules.get("_common.service_heartbeat")
    so an unregistered importer never pays the db_commons import."""
    for service in _threads:
        return service
    return None


def _write_stop(service: str) -> None:
    """Stop the service's beat thread, then record its exit (best effort)."""
    thread = _threads.pop(service, None)
    if thread is None:
        return
    # The mailbox state dies with the thread's registration (Phase 4):
    # a stale state left behind would hold a consumed command edge for
    # a service this process no longer runs.
    _command_states.pop(service, None)
    _last_progress.pop(service, None)
    reason, normal = _exit_info.pop(service, ("process exit", True))
    thread.stop()
    thread.join(timeout=3.0)  # no in-flight beat can clear the stop row
    # Scope from the thread's OWN registration — the registry only knows
    # the always-on services; ad-hoc/batch services would else land as
    # 'unknown' on the no-row-yet INSERT path.
    reg_scope, reg_sub = thread._scope, thread._sub_scope
    global _planned_stop_reason
    planned = _planned_stop_reason
    _planned_stop_reason = None
    if not normal and not planned:
        # An ABNORMAL stop is a caution (monitor.caution_log, the
        # Cautions tab's working set): every pipeline / batch / daemon
        # run that died non-zero, on a signal, or on an unhandled
        # exception lands here with its exit reason as context. A
        # PLANNED stop (supervisor restart — marker consumed by
        # _on_sigterm) is exempt: the supervisor logs the restart
        # itself, this row would only duplicate it at error severity.
        # Flush synchronously — this is the exit path, the daemon writer
        # would not survive interpreter shutdown.
        from _common.caution_log import flush as _caution_flush
        from _common.caution_log import record_service_error
        record_service_error(
            f"{service} stopped abnormally: {reason}",
            dedupe_key=f"abnormal-stop:{reason.split(':')[0]}",
            detail={"scope": reg_scope, "sub_scope": reg_sub,
                    "pid": os.getpid(), "stop_reason": reason},
            service=service,
        )
        _caution_flush(timeout_s=5.0)
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    _STOP_SQL,
                    (
                        service,
                        reg_scope or "unknown",
                        reg_sub,
                        os.getpid(),
                        reason,
                        normal,
                    ),
                )
            audit_id = _audit_ids.pop(service, None)
            if audit_id is not None:
                with conn.cursor() as cur:
                    cur.execute(_AUDIT_CLOSE_SQL, (reason, normal, audit_id))
            _release_parallel_slot(service)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — best effort at exit time
        print(
            f"[service_heartbeat] {service}: stop record failed: {e}",
            file=sys.stderr,
            flush=True,
        )


def _write_all_stops() -> None:
    for service in list(_threads):
        _write_stop(service)


def _note_all(reason: str, normal: bool) -> None:
    for service in _threads:
        _exit_info.setdefault(service, (reason, normal))


def _install_exit_hooks() -> None:
    """Install the atexit/SIGTERM/excepthook hooks once, chaining any
    handler that existed before (the app keeps its own semantics)."""
    global _hooks_installed, _prev_sigterm, _prev_excepthook
    with _hooks_lock:
        if _hooks_installed:
            return
        atexit.register(_write_all_stops)

        def _on_sigterm(signum, frame):  # noqa: ANN001, ARG001
            global _planned_stop_reason
            _planned_stop_reason = _consume_planned_stop(os.getpid())
            _note_all(f"signal {signal.Signals(signum).name}", False)
            if callable(_prev_sigterm):
                _prev_sigterm(signum, frame)
            # Graceful exit so atexit can flush the stop record (the
            # default SIGTERM disposition would skip it).
            raise SystemExit(128 + signum)

        try:
            _prev_sigterm = signal.signal(signal.SIGTERM, _on_sigterm)
        except ValueError:  # not the main thread — skip signal hook
            pass

        if _prev_excepthook is None:
            _prev_excepthook = sys.excepthook

        def _on_exception(tp, val, tb):  # noqa: ANN001
            _note_all(f"unhandled exception: {tp.__name__}: {val}", False)
            _prev_excepthook(tp, val, tb)

        sys.excepthook = _on_exception
        _hooks_installed = True


# ---------------------------------------------------------------------------
# max_parallel / live_parallel + the parallel-services lists — the
# concurrency guard
# ---------------------------------------------------------------------------
# The CAP is static JSON config (database/sql/monitoring/static/
# service_config/ — a per-service DIRECTORY TREE like
# external_request_config/: the dotted service name is the path, each file
# one object {service, max_parallel, included_parallel_services,
# excluded_parallel_services, is_resident, freq, priority, schedule,
# notes, source_ref, updated_at} — is_resident true marks an ALWAYS-LIVE
# service (freq null: cadence does not apply); false marks a one-time
# task whose freq orchestrates when it starts; the tree-root default.json
# is the FALLBACK carrying the repo-wide cap and the repo-wide parallel
# lists), read at registration;
# the COUNTER (live_parallel) lives on the service's monitor.service_heartbeat
# row — the current-state row the claim's conditional UPDATE serializes on.
# Registration claims a slot past the cap and auto-exits the newcomer; a
# recorded exit releases it; the stream watchdog resets the counter from
# pid liveness when a process is detected down without an exit record (a
# dead process cannot release its own slot). The two PARALLEL-SERVICES
# lists (see the module docstring) are read from the same files and
# checked cross-service right after the cap claim — a conflict releases
# the just-claimed slot and auto-exits too.

_STATIC_SERVICE_CONFIG_DIR = (
    _REPO_ROOT / "database" / "sql" / "monitoring" / "static"
    / "service_config"
)

DEFAULT_MAX_PARALLEL = 1


def _service_config_files(service: str) -> tuple[Path, ...]:
    """The service's own service_config/ tree file first
    (``downloads.stream.szse.price`` → downloads/stream/szse/price.json),
    then the tree-root default.json fallback. A service name that is not a
    clean dotted module path (segments outside [A-Za-z0-9_-], or a ``..``
    traversal) skips its own file — only the fallback is consulted."""
    files: list[Path] = []
    segments = service.split(".")
    if service and all(
        s and all(c.isalnum() or c in "_-" for c in s) for s in segments
    ):
        files.append(
            _STATIC_SERVICE_CONFIG_DIR.joinpath(*segments).with_suffix(".json")
        )
    files.append(_STATIC_SERVICE_CONFIG_DIR / "default.json")
    return tuple(files)


def max_parallel_for(service: str) -> int:
    """The service's concurrency cap from the static service_config/ tree.

    the service's own file's ``max_parallel`` when it has one, else the
    tree-root default.json fallback's, else DEFAULT_MAX_PARALLEL. Any
    problem reading (missing, unreadable, malformed) falls through to the
    next source — a config problem must never block a pipeline, same
    contract as the DB-unreachable "skipped" claim path.
    """
    for file in _service_config_files(service):
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            cap = doc.get("max_parallel")
            if isinstance(cap, int) and cap >= 1:
                return cap
    return DEFAULT_MAX_PARALLEL


DEFAULT_STALE_PROCESS_CHECK = True


def stale_check_for(service: str) -> bool:
    """Whether the stream watchdog may restart this service for a STALE
    PROCESS (its heartbeat's code_fingerprint no longer matches the
    on-disk sources — the service kept running old code after an edit).
    The service's own service_config/ file's ``stale_process_check``
    when present (false opts OUT — e.g. a service whose restarts must
    be strictly manual), else the tree-root default.json's, else
    DEFAULT_STALE_PROCESS_CHECK. Same read-fail-soft contract as
    max_parallel_for: a config problem never disables supervision."""
    for file in _service_config_files(service):
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            flag = doc.get("stale_process_check")
            if isinstance(flag, bool):
                return flag
    return DEFAULT_STALE_PROCESS_CHECK


# --- resource class + commands + progress config (2026-11-21 control
#     unification plan Phases 3+4) ---------------------------------------
#
# The COMMAND/PROGRESS channel is gated by CLASS, never by code branches:
# each service's service_config file carries ``resource_class`` +
# ``pausable`` (and, when it has derivable progress, a ``progress``
# block). Classes (the memory study's gating):
#   io      — pure-I/O downloads (0/62 activate cudf): PAUSE/RESUME OK,
#             a pause holds ~RAM only; bounded by ``max_pause_s``.
#   sampler — intraday stream samplers: pause allowed ONLY outside a
#             trading session (mid-session = silent data loss — the
#             child refuses with that reason; the flag stays true).
#   host    — resident hosts: a pause holds the whole RSS (generous
#             bound, rarely wanted).
#   gpu     — NO pause (a paused GPU step pins the CUDA context ~1.67
#             GiB + the WSL early floor until exit — kill-and-restart
#             stays its only give-way).
# An UNCONFIGURED service defaults to the gpu class + not pausable:
# refusing a command is always the safe default (Phase 4 lands behind
# per-service opt-in flags).
RESOURCE_CLASSES: tuple[str, ...] = ("io", "sampler", "host", "gpu")
DEFAULT_RESOURCE_CLASS = "gpu"
DEFAULT_PAUSABLE = False

# The pause bound (seconds) when a service_config file carries
# ``max_pause_s``; the class default caps a pause when unset (io/sampler
# 1800s, host 600s — a resident pause must not outlive the operator's
# attention; gpu never pauses).
DEFAULT_MAX_PAUSE_S = 1800.0
_HOST_MAX_PAUSE_S = 600.0

# A ``progress`` block's table-name charset — config is git-reviewed,
# but the names ride INTO SQL as fragments, so validate the charset
# defensively anyway (segments of [A-Za-z0-9_], dotted, schema-qualified).
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def resource_class_for(service: str) -> str:
    """The service's resource class from the static service_config tree
    (``resource_class`` in its own file; absent → the gpu no-pause
    default). Same file-then-default.json chain and fail-soft contract
    as max_parallel_for."""
    for file in _service_config_files(service):
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            cls = doc.get("resource_class")
            if cls in RESOURCE_CLASSES:
                return cls
    return DEFAULT_RESOURCE_CLASS


def pausable_for(service: str) -> bool:
    """Whether the service accepts PAUSE/RESUME commands at all (its
    service_config file's ``pausable``; absent → not pausable). The
    class still gates HOW a pause behaves (sampler session checks, gpu
    refusal) — this flag is the opt-in switch."""
    for file in _service_config_files(service):
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            flag = doc.get("pausable")
            if isinstance(flag, bool):
                return flag
    return DEFAULT_PAUSABLE


def max_pause_s_for(service: str) -> float:
    """The pause bound for the service: its service_config file's
    ``max_pause_s`` when present, else the class default (io/sampler
    1800s, host 600s). A pause auto-resumes past the bound — a wedged
    pause can never hold a resident forever."""
    for file in _service_config_files(service):
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            bound = doc.get("max_pause_s")
            if isinstance(bound, (int, float)) and bound > 0:
                return float(bound)
    cls = resource_class_for(service)
    if cls == "host":
        return _HOST_MAX_PAUSE_S
    return DEFAULT_MAX_PAUSE_S


def _clean_progress_tables(value: object) -> dict[str, str]:
    """One progress config entry → the validated table/column fields
    (strings fitting the table-name charset; anything else dropped)."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("identity_table", "intraday_table", "result_table",
                "key_column", "code_column", "exchange_column"):
        val = value.get(key)
        if isinstance(val, str) and _TABLE_NAME_RE.fullmatch(val):
            out[key] = val
    return out


def service_progress_config(service: str) -> dict | None:
    """The service's derived-progress config from its service_config
    file's ``progress`` block, validated minimally; None when absent or
    malformed (no derived progress is derived then — a config problem
    must never break the detail endpoint). Shapes:

      {"kind": "intraday", "assets": [{name, identity_table,
        intraday_table, code_column?, exchange_column?, exchange?}]}
      — the stream-poller completeness (one render of the shared
        intraday_progress template per asset).
      {"kind": "missing_set", "identity_table": ..., "result_table":
        ..., "key_column": ...}
      — the generic expected-minus-missing oracle (identity vs result
        table on the key column, latest date).
    """
    for file in _service_config_files(service):
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        block = doc.get("progress")
        if not isinstance(block, dict):
            continue
        kind = block.get("kind")
        if kind == "intraday":
            assets = []
            for entry in block.get("assets") or []:
                fields = _clean_progress_tables(entry)
                if not {"identity_table", "intraday_table"} <= fields.keys():
                    continue  # a malformed asset drops silently
                exchange = entry.get("exchange") if isinstance(
                    entry, dict) else None
                assets.append({
                    "name": (entry.get("name") if isinstance(entry, dict)
                             else "") or fields["intraday_table"],
                    "identity_table": fields["identity_table"],
                    "intraday_table": fields["intraday_table"],
                    "code_column": fields.get("code_column", "code"),
                    "exchange_column": fields.get("exchange_column"),
                    "exchange": exchange if isinstance(exchange, str)
                    else None,
                })
            if assets:
                return {"kind": "intraday", "assets": assets}
            return None
        if kind == "missing_set":
            fields = _clean_progress_tables(block)
            if {"identity_table", "result_table"} <= fields.keys():
                return {"kind": "missing_set",
                        "identity_table": fields["identity_table"],
                        "result_table": fields["result_table"],
                        "key_column": fields.get("key_column", "code")}
            return None
        return None
    return None


# --- resident / freq / priority (the orchestration give-way rank) ----------
#
# Higher integer = higher priority: the orchestration runner's give-way
# board (server_cluster.monitor.orchestration) holds back / stops a step
# while a STRICTLY higher-priority step of another run is in flight. The
# rank encodes the two house rules: the DOWNLOADS domain outranks every
# non-downloads service regardless of cadence (a build gives way to a
# download), and within a domain the more frequent the cadence the higher
# the priority (a RESIDENT outranks daily; daily outranks weekly
# outranks monthly ...; an unknown freq ranks last, with on_demand).

# The always-live band: is_resident services (the 2026-11-20 flag that
# replaced the former freq "always" labeling) rank here — the number the
# residents' config files already carried explicitly.
RESIDENT_PRIORITY = 80

FREQ_PRIORITY: dict[str, int] = {
    "daily": 70,
    "weekly": 60,
    "monthly": 50,
    "quarterly": 40,
    "semiannually": 30,
    "once": 20,
    "occasionally": 10,
    "on_demand": 0,
}

DOWNLOADS_PRIORITY_BONUS = 100
DEFAULT_PRIORITY = 0

DEFAULT_IS_RESIDENT = False


def is_resident_for(service: str) -> bool:
    """Whether the service is a RESIDENT (``is_resident`` true in its
    service_config/ file): an always-live daemon the resident supervisor
    / ensure-up path keeps up — no cadence, ``freq`` does not apply
    (null in the file). Same file-then-default.json chain and fail-soft
    contract as :func:`max_parallel_for`: a config problem never changes
    behavior — the fallback treats an unknown service as a one-time
    task."""
    for file in _service_config_files(service):
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            flag = doc.get("is_resident")
            if isinstance(flag, bool):
                return flag
    return DEFAULT_IS_RESIDENT


def freq_for(service: str) -> str | None:
    """The service's effective freq from the static service_config/ tree
    — None for a RESIDENT (is_resident true — an always-live service has
    no cadence) or when unknown; same file-then-default.json chain and
    fail-soft contract as :func:`priority_for` (a config problem just
    yields None: no cadence known, so the arrangement runs the service,
    never silently skips it)."""
    if is_resident_for(service):
        return None
    for file in _service_config_files(service):
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("freq"), str):
            return doc["freq"]
    return None


def has_own_service_config(service: str) -> bool:
    """Whether the service has its OWN file in the service_config/ tree
    (not the tree-root default.json fallback). The arranger's safeguard:
    a tier step without its own config is always arranged due — an
    unconfigured step keeps the run-every-pass behavior instead of being
    silently skipped off the default.json ``on_demand`` freq."""
    return _service_config_files(service)[0].is_file()


def derive_priority(service: str, freq: object,
                    is_resident: bool = False) -> int:
    """The canonical priority of ``service`` — the resident band when it
    always lives (``is_resident``), else the freq rank; plus the
    downloads-domain bonus (keyed off the service NAME's first segment,
    so a config-less ``downloads.*`` ad-hoc run still ranks in the
    downloads band). An unknown freq ranks with on_demand."""
    base = (RESIDENT_PRIORITY if is_resident
            else FREQ_PRIORITY.get(freq) if isinstance(freq, str) else None)
    return (
        (base if base is not None else FREQ_PRIORITY["on_demand"])
        + (DOWNLOADS_PRIORITY_BONUS
           if service.startswith("downloads.") else 0)
    )


def priority_for(service: str) -> int:
    """The service's orchestration priority from the static
    service_config/ tree — same file-then-default.json chain and
    fail-soft contract as max_parallel_for. The file's explicit
    ``priority`` int wins (a hand override — keep it in sync with the
    file's is_resident/freq when editing); else the value DERIVED from
    that same file's ``is_resident``/``freq`` plus the service name's
    domain; else the next file in the chain; else DEFAULT_PRIORITY (a
    config problem never changes behavior, it just ranks the service
    last)."""
    for file in _service_config_files(service):
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            prio = doc.get("priority")
            if isinstance(prio, int) and prio >= 0:
                return prio
            if isinstance(doc.get("is_resident"), bool) or isinstance(
                    doc.get("freq"), str):
                return derive_priority(
                    service, doc.get("freq"),
                    is_resident=doc.get("is_resident") is True)
    return DEFAULT_PRIORITY


def _clean_parallel_entries(value: object) -> tuple[str, ...]:
    """JSON list → validated dotted-prefix entries (same segment charset as
    a service name — [A-Za-z0-9_-]). Anything else in the file is dropped:
    a malformed entry must never block a pipeline."""
    if not isinstance(value, list):
        return ()
    return tuple(
        v
        for v in value
        if isinstance(v, str)
        and v
        and all(
            s and all(c.isalnum() or c in "_-" for c in s)
            for s in v.split(".")
        )
    )


def parallel_services_for(
    service: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The service's (included, excluded) parallel-services lists from the
    static service_config/ tree — same file-then-default.json chain as
    max_parallel_for, same tolerance (missing/unreadable → empty). An empty
    included list means unrestricted; a non-empty one is a whitelist of the
    services that MAY run in parallel. Entries are rooted dotted prefixes
    of a service name: a scope ("downloads"), a sub-scope path
    ("downloads.stock" — two or more segments), or the full service name.
    """
    for file in _service_config_files(service):
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            return (
                _clean_parallel_entries(
                    doc.get("included_parallel_services")
                ),
                _clean_parallel_entries(
                    doc.get("excluded_parallel_services")
                ),
            )
    return ((), ())


def _match_specificity(entry: str, other: str) -> int:
    """Segment count when ``entry`` matches ``other`` as a rooted dotted
    prefix, else 0 — the priority order (full name > sub-scope path >
    scope) IS the count."""
    e = entry.split(".")
    return len(e) if other.split(".")[: len(e)] == e else 0


def _blocked_by(
    included: tuple[str, ...],
    excluded: tuple[str, ...],
    other: str,
) -> tuple[str, str] | None:
    """Pair verdict — (blocking list, matched entry) when ``other`` may NOT
    run in parallel with the owner of the two lists, else None. A matching
    exclusion always blocks unless an inclusion matches at STRICTLY higher
    specificity (service name > sub-scope > scope; a same-specificity tie
    resolves to excluded — fail-safe). A non-empty included list is a
    whitelist: an unlisted ``other`` blocks with no single entry ("").
    """
    exc_best = max(
        ((_match_specificity(e, other), e) for e in excluded), default=(0, "")
    )
    inc_best = max(
        ((_match_specificity(e, other), e) for e in included), default=(0, "")
    )
    if exc_best[0] > 0 and exc_best[0] >= inc_best[0]:
        return "excluded_parallel_services", exc_best[1]
    if inc_best[0] > 0:
        return None
    if included:
        return "included_parallel_services", ""
    return None


_RUNNING_SERVICES_SQL = """
SELECT service
FROM monitor.service_heartbeat
WHERE service <> %s
  AND stop_reason IS NULL
  AND beat_at > now() - make_interval(secs => %s)
"""


def _parallel_conflicts(service: str) -> list[str]:
    """Human-readable parallel-services conflicts for starting ``service``
    now, both directions: its lists against every live service, and every
    live service's lists against it (so an exclusion holds regardless of
    start order). A live service is the UI's definition mirrored — a
    heartbeat row with no stop record and a beat inside STALE_AFTER_S.
    A DB problem returns [] (check skipped, run admitted): same contract
    as the max_parallel claim's "skipped" state."""
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(_RUNNING_SERVICES_SQL, (service, STALE_AFTER_S))
                running = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — never block on a DB problem
        print(
            f"[service_heartbeat] {service}: parallel-services check "
            f"skipped (DB unreachable: {e})",
            file=sys.stderr,
            flush=True,
        )
        return []
    own_inc, own_exc = parallel_services_for(service)
    conflicts: list[str] = []
    for other in running:
        blocked = _blocked_by(own_inc, own_exc, other)
        if blocked is not None:
            detail = (
                f"entry '{blocked[1]}'" if blocked[1]
                else "whitelist active, service not listed"
            )
            conflicts.append(
                f"{other} (running) — {service}'s {blocked[0]}: {detail}"
            )
            continue
        other_inc, other_exc = parallel_services_for(other)
        blocked = _blocked_by(other_inc, other_exc, service)
        if blocked is not None:
            detail = (
                f"entry '{blocked[1]}'" if blocked[1]
                else "whitelist active, service not listed"
            )
            conflicts.append(
                f"{service} — {other}'s (running) {blocked[0]}: {detail}"
            )
    return conflicts


_CLAIM_SQL = f"""
UPDATE {HEARTBEAT_TABLE}
SET live_parallel = live_parallel + 1,
    pid = %s,
    started_at = now(),
    beat_at = now()
WHERE service = %s AND live_parallel < %s
RETURNING live_parallel
"""

# Open audit lives = registered processes with no exit record; a dead
# one leaves its row open (SIGKILL / WSL shutdown cannot close it) until
# the watchdog's orphan pass — the same source the watchdog's
# _reset_live_parallel counts, so the two heals agree by construction.
_OPEN_LIVES_PIDS_SQL = """
SELECT DISTINCT pid
FROM monitor.service_audit
WHERE service = %s
  AND ended_at IS NULL
"""

_ROW_PID_BEAT_SQL = (
    f"SELECT pid, EXTRACT(EPOCH FROM (now() - beat_at))::bigint "
    f"FROM {HEARTBEAT_TABLE} WHERE service = %s"
)


def _claim_attempt(cur, service: str, cap: int, pid: int) -> tuple[int] | None:
    """One race-free check-and-increment of ``live_parallel`` (the claim
    IS the check: simultaneous registrations serialize on the row). The
    claim also takes over the row's identity columns: pid = the claimant
    (a claimant that has not beaten yet must still be VISIBLE — to the
    liveness heal below, which would otherwise count only audit lives
    and admit a duplicate into the claim→audit-insert gap, and to the
    watchdog, whose stale-beat pass would otherwise see a dead pid and
    double-spawn during the pre-first-beat window), started_at/beat_at =
    now() — the same honesty the first-sight INSERT documents: the
    process IS starting; a crash before the first real beat leaves a row
    the watchdog's orphan pass marks stopped."""
    cur.execute(_CLAIM_SQL, (pid, service, cap))
    return cur.fetchone()


def _heal_leaked_parallel_slot(cur, service: str) -> bool:
    """Rebuild ``live_parallel`` from the claimants' host liveness — the
    open audit lives plus the heartbeat row's own pid (a claimant that
    has not inserted its audit life yet is still visible through the row
    pid it stamped at claim time) — the registration-time twin of the
    watchdog's _reset_live_parallel (same pid + /proc-cmdline test; a
    Windows host without /proc reports every pid dead, so the heal
    always fires there — all real pipelines register inside the repo's
    WSL distro, where /proc exists). Returns True when the counter
    CHANGED, i.e. a leaked value was repaired; a genuinely-running
    holder keeps its count and the caller's refusal stands (no duplicate
    is admitted).

    A claimant counts as alive only while the row's beat is ALSO fresh:
    a genuinely-running holder beats every HEARTBEAT_INTERVAL_S, so a
    stale beat proves there is NO live holder no matter what /proc says.
    The /proc test alone false-positives on WSL's aggressive pid
    recycling — a stale open-audit pid routinely points at an unrelated
    fresh process after the distro restarts (2026-10-23: exactly that
    deadlocked the stream watchdog's own restart — the heal refused to
    zero the leaked slot, the starter auto-exited, and the always-on
    stream stayed down). A /proc-alive pid with a stale beat is a wedged
    runtime — the stream watchdog's kill path, not something a newcomer
    must wait behind.

    Assumption shared with the watchdog's reset: an open audit life is a
    faithful liveness record (a registration whose audit INSERT failed
    would be counted dead — DB hiccups at registration only).
    """
    cur.execute(_OPEN_LIVES_PIDS_SQL, (service,))
    pids = {r[0] for r in cur.fetchall()}
    cur.execute(_ROW_PID_BEAT_SQL, (service,))
    row = cur.fetchone()
    if row is not None and row[0] is not None:
        pids.add(row[0])
    # Row-level gate (the beat is per service, not per pid): with no
    # fresh beat no claimant can be a live holder; with one, the pid
    # test picks the survivors (a fresh-beating sibling keeps its slot,
    # a dead co-claimant does not).
    beat_fresh = row is not None and row[1] is not None and row[1] <= STALE_AFTER_S
    alive = (
        [p for p in pids if pid_alive_for_service(p, service)]
        if beat_fresh
        else []
    )
    cur.execute(
        f"UPDATE {HEARTBEAT_TABLE} SET live_parallel = %s "
        f"WHERE service = %s AND live_parallel <> %s",
        (len(alive), service, len(alive)),
    )
    healed = cur.rowcount > 0
    if healed:
        print(
            f"[service_heartbeat] {service}: live_parallel was leaked at "
            f"the cap (holder died without an exit record) — rebuilt from "
            f"claimant liveness: {len(alive)} alive",
            file=sys.stderr,
            flush=True,
        )
    return healed


def _claim_parallel_slot(service: str) -> tuple[str, int, int]:
    """Atomically claim one parallel slot for ``service``.

    Returns (state, live_parallel, max_parallel) with state:
      "claimed" — live_parallel was incremented past the cap check. The
                  single-statement conditional UPDATE is the race-free
                  check-and-increment: simultaneous registrations of the
                  same module serialize on the one heartbeat row.
      "refused" — cap reached, NOTHING was incremented (the caller
                  auto-exits) — after one liveness heal of a leaked
                  counter, see _heal_leaked_parallel_slot.
      "skipped" — the DB was unreachable; the run is admitted UNCOUNTED
                  (a heartbeat outage must not stop pipelines). The
                  watchdog's process-down reset rebuilds live_parallel
                  from pid liveness, healing uncounted and leaked slots.
    """
    cap = max_parallel_for(service)
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                # First sight of the service: create its heartbeat row
                # (the counter's home). beat_at=now() is honest — the
                # process IS starting; a crash before the first real beat
                # leaves a row the watchdog's orphan pass marks stopped.
                cur.execute(
                    f"INSERT INTO {HEARTBEAT_TABLE} "
                    "(service, pid, started_at, beat_at) "
                    "VALUES (%s, %s, now(), now()) "
                    "ON CONFLICT (service) DO NOTHING",
                    (service, os.getpid()),
                )
                row = _claim_attempt(cur, service, cap, os.getpid())
                if row is None:
                    # Cap reached — but a holder that died WITHOUT an exit
                    # record (WSL shutdown, SIGKILL, OOM) cannot release
                    # its slot: the counter then leaks AT the cap and the
                    # service could never start again (the watchdog heals
                    # on process-down detection, but the watchdog is
                    # itself a service — the all-dead recovery deadlock).
                    # Rebuild the counter from the claimants' host
                    # liveness and retry once.
                    if _heal_leaked_parallel_slot(cur, service):
                        row = _claim_attempt(cur, service, cap, os.getpid())
                if row is not None:
                    return "claimed", row[0], cap
                cur.execute(
                    f"SELECT live_parallel FROM {HEARTBEAT_TABLE} "
                    "WHERE service = %s",
                    (service,),
                )
                return "refused", cur.fetchone()[0], cap
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — never block on a DB problem
        print(
            f"[service_heartbeat] {service}: max_parallel check skipped "
            f"(DB unreachable: {e})",
            file=sys.stderr,
            flush=True,
        )
        return "skipped", 0, cap


def _release_parallel_slot(service: str) -> None:
    """Release the slot claimed at registration (best effort). A lost
    release (DB down at exit) leaves live_parallel one high — the
    watchdog's process-down reset heals it on the service's next detected
    death."""
    if not _slots_claimed.pop(service, None):
        return  # admitted uncounted (DB down at claim) — nothing to release
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {HEARTBEAT_TABLE} "
                    "SET live_parallel = GREATEST(live_parallel - 1, 0) "
                    "WHERE service = %s",
                    (service,),
                )
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — best effort at exit time
        print(
            f"[service_heartbeat] {service}: live_parallel release "
            f"failed: {e}",
            file=sys.stderr,
            flush=True,
        )


# ---------------------------------------------------------------------------
# Command mailbox + reported progress — the Phase 4 channels (2026-11-21
# control unification plan)
# ---------------------------------------------------------------------------
# The DOWN channel: the heartbeat thread claims commands from
# monitor.service_command every ``_MAILBOX_POLL_S`` (ONE conditional
# UPDATE claims AND consumes atomically — exactly-once, the
# live_parallel-claim pattern composed with the planned-stop marker's
# 120s age cap for expiry) and dispatches into the in-process state the
# WORK loop reads at its safe checkpoints (:func:`pause_checkpoint` /
# :func:`consume_flush`). Class-gated from the memory study:
# ``pausable_for`` / ``resource_class_for`` (the service_config tree's
# ``pausable`` / ``resource_class`` flags) decide whether a PAUSE is
# honored at all (gpu never; an unconfigured service defaults to gpu) —
# and a SAMPLER's checkpoints pass ``safe=False`` mid-session, so a
# pause that would silently lose data is refused with that reason.
#
# The UP channel: the work loop posts progress at its safe checkpoints
# (:func:`report_progress` — throttled; a phase/state change posts
# immediately) straight into the heartbeat row's progress columns
# (guarded on pid so a concurrent life cannot overwrite another's
# report). The detail endpoint reads them; the hung detector reads
# ``paused`` as the legal state it is.
#
# Fail-soft contract (the plan's risk register): a DB outage means NO
# commands flow and progress posts skip — work proceeds, the same as
# beats. SIGTERM → grace → SIGKILL remains the backstop everywhere.

_MAILBOX_POLL_S = 5.0
_COMMAND_TTL_S = 120.0
_PROGRESS_MIN_INTERVAL_S = 30.0

_CLAIM_COMMAND_SQL = """
UPDATE monitor.service_command
SET claimed_at = now(), claimed_pid = %s, consumed_at = now()
WHERE id = (
    SELECT id FROM monitor.service_command
    WHERE service = %s
      AND consumed_at IS NULL
      AND expires_at > now()
    ORDER BY id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING id, command, payload
"""

_REPORT_PROGRESS_SQL = """
UPDATE monitor.service_heartbeat
SET progress_phase = COALESCE(%s, progress_phase),
    progress_state = COALESCE(%s, progress_state),
    progress_payload = COALESCE(%s::jsonb, progress_payload),
    progress_at = now()
WHERE service = %s AND pid = %s
"""


class ServiceCommandState:
    """The in-process mailbox state ONE registered service's work loop
    reads at its safe checkpoints. The heartbeat thread is the only
    WRITER (dispatch); the work loop is the only ACTOR (the
    checkpoint blocks / the flush edge fires)."""

    def __init__(self, service: str) -> None:
        self.service = service
        self.pause_requested = False
        self.pause_deadline = 0.0  # monotonic — the auto-resume bound
        self.paused = False  # True while a pause actually holds
        self.flush_requested = False  # the FLUSH_NOW edge (one-shot)
        self.lock = threading.Lock()


_command_states: dict[str, ServiceCommandState] = {}
_progress_lock = threading.Lock()
_last_progress: dict[str, tuple[float, tuple]] = {}


def _dispatch_command(state: ServiceCommandState, command_code: int,
                      payload: Any) -> None:
    """One claimed command → the in-process state (the heartbeat
    thread's side; the checkpoint does the holding). Field writes ride
    the state's lock — the work loop reads the same fields at its
    checkpoints."""
    with state.lock:
        if command_code == SERVICE_COMMAND_CODE["flush_now"]:
            state.flush_requested = True
            print(
                f"[service_heartbeat] {state.service}: FLUSH_NOW claimed "
                "— the flush edge is set for the next cycle tail",
                file=sys.stderr, flush=True,
            )
            return
        if command_code == SERVICE_COMMAND_CODE["resume"]:
            state.pause_requested = False
            state.pause_deadline = 0.0
            state.paused = False
            print(
                f"[service_heartbeat] {state.service}: RESUME claimed — "
                "the checkpoint releases",
                file=sys.stderr, flush=True,
            )
            # Post running NOW (not just at the checkpoint's release): a
            # child whose next safe checkpoint is far away (a poller
            # sleeping until the next session) must not sit in the
            # reported `pausing` state for days after a resume.
            report_progress(state="running", service=state.service,
                            force=True)
            return
        if command_code == SERVICE_COMMAND_CODE["pause"]:
            if not pausable_for(state.service) \
                    or resource_class_for(state.service) == "gpu":
                print(
                    f"[service_heartbeat] {state.service}: PAUSE refused "
                    "(not pausable or gpu-class — kill-and-restart stays "
                    "its only give-way); command consumed, nothing held",
                    file=sys.stderr, flush=True,
                )
                return
            bound = max_pause_s_for(state.service)
            override = (payload or {}).get("max_pause_s") \
                if isinstance(payload, dict) else None
            if isinstance(override, (int, float)) and 0 < override <= bound:
                bound = float(override)
            state.pause_deadline = time.monotonic() + bound
            state.pause_requested = True
        else:
            print(
                f"[service_heartbeat] {state.service}: unknown command "
                f"code {command_code} consumed (no-op)",
                file=sys.stderr, flush=True,
            )
            return
    # Outside the lock: the DB post is best effort and the bound print
    # is already computed.
    print(
        f"[service_heartbeat] {state.service}: PAUSE claimed — holds at "
        f"the next safe checkpoint, auto-resumes after {bound:.0f}s",
        file=sys.stderr, flush=True,
    )
    report_progress(state="pausing", service=state.service, force=True)


def report_progress(phase: str | None = None, state: str | None = None,
                    payload: dict | None = None, *,
                    service: str | None = None, force: bool = False,
                    ) -> bool:
    """Post THIS process's progress into its heartbeat row (the Phase 4
    UP channel). ``phase`` is the three-phase machine (downloading |
    reading | writing), ``state`` the run state (running | pausing |
    paused | done); omitted fields KEEP their previous values (the
    COALESCE in the SQL), so a checkpoint can update one dimension.
    Throttled: an unchanged (phase, state) reposts at most every
    ``_PROGRESS_MIN_INTERVAL_S`` unless ``force`` (phase transitions
    and checkpoint state flips force).

    Guarded on pid: a concurrent life of the same service cannot
    overwrite another life's report. Returns True when a row was
    written; False = throttled away, nothing registered, or the DB is
    unreachable (fail-soft — progress is a bonus, never a blocker)."""
    svc = service or current_service()
    if svc is None:
        return False
    phase_code = PROGRESS_PHASE_CODE.get(phase) if phase else None
    if phase is not None and phase_code is None:
        raise ValueError(f"unknown progress phase {phase!r}")
    state_code = PROGRESS_STATE_CODE.get(state) if state else None
    if state is not None and state_code is None:
        raise ValueError(f"unknown progress state {state!r}")
    now = time.monotonic()
    signature = (phase_code, state_code)
    with _progress_lock:
        last_time, last_sig = _last_progress.get(svc, (0.0, None))
        if not force and signature == last_sig \
                and now - last_time < _PROGRESS_MIN_INTERVAL_S:
            return False
        _last_progress[svc] = (now, signature)
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    _REPORT_PROGRESS_SQL,
                    (phase_code, state_code,
                     json.dumps(payload) if payload else None,
                     svc, os.getpid()),
                )
                return cur.rowcount > 0
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — progress is fail-soft
        print(
            f"[service_heartbeat] {svc}: progress post failed: {e}",
            file=sys.stderr, flush=True,
        )
        return False


def pause_checkpoint(*, safe: bool = True,
                     service: str | None = None) -> bool:
    """The safe-checkpoint PAUSE hold (the Phase 4 DOWN channel's work-
    loop side). Call at every safe checkpoint of a pausable service's
    loop (the poller cycle tail, the batch stage boundary); blocks
    while a claimed PAUSE holds, auto-resumes past the ``max_pause_s``
    bound, reports ``paused`` / ``running`` around the hold.

    ``safe=False`` marks a checkpoint where a pause would SILENTLY
    LOSE DATA (a sampler mid-session): a pause requested there is
    refused with that reason and ``False`` returned. A service with no
    registered mailbox state returns False immediately (no-op).

    Returns True when a pause was honored (held and released)."""
    svc = service or current_service()
    if svc is None:
        return False
    st = _command_states.get(svc)
    if st is None:
        return False
    honored = False
    while True:
        with st.lock:
            requested = st.pause_requested
            deadline = st.pause_deadline
        if not requested:
            if honored:
                st.paused = False
                # The release drain (Phase 4's re-wiring of the orphaned
                # post_check.release_memory — gap 9): a held pause held
                # buffers/pools; release them back BEFORE the loop
                # resumes (also the future gpu-class drain-pause tier's
                # hook point). Best effort — a drain problem never
                # blocks the resume.
                try:
                    from _common.post_check import release_memory

                    release_memory()
                except Exception as exc:  # noqa: BLE001 — best effort
                    print(
                        f"[service_heartbeat] {svc}: pause-release drain "
                        f"failed: {exc}",
                        file=sys.stderr, flush=True,
                    )
                report_progress(state="running", service=svc, force=True)
            return honored
        if not safe:
            st.pause_requested = False
            print(
                f"[service_heartbeat] {svc}: PAUSE refused at an unsafe "
                "checkpoint (mid-session sampler — a hold here would "
                "silently lose data)",
                file=sys.stderr, flush=True,
            )
            report_progress(state="running", service=svc, force=True)
            return False
        if time.monotonic() >= deadline:
            st.pause_requested = False
            st.paused = False
            print(
                f"[service_heartbeat] {svc}: pause bound reached — "
                "auto-resumed",
                file=sys.stderr, flush=True,
            )
            report_progress(state="running", service=svc, force=True)
            return honored
        if not honored:
            honored = True
            st.paused = True
            report_progress(state="paused", service=svc, force=True)
        time.sleep(1.0)


def consume_flush(service: str | None = None) -> bool:
    """One-shot FLUSH_NOW edge (the poller cycle tail's check): True
    exactly once per claimed FLUSH_NOW command."""
    svc = service or current_service()
    if svc is None:
        return False
    st = _command_states.get(svc)
    if st is None:
        return False
    with st.lock:
        if st.flush_requested:
            st.flush_requested = False
            return True
    return False


def command_state_for(service: str) -> ServiceCommandState | None:
    """The in-process mailbox state of ``service`` (None when
    unregistered) — read by the supervisor's hung pass via the DB, not
    this; kept for probes/tests."""
    return _command_states.get(service)


def _enforce_parallel_policy(service: str) -> None:
    """Exit when the service's ``max_parallel`` is already reached, or a
    parallel-services conflict exists with a live service.

    The cap check IS the claim (_claim_parallel_slot); the cross-service
    lists run right after a successful claim — a conflict releases the
    just-claimed slot and raises SystemExit (BaseException — deliberately
    NOT swallowed by the best-effort ``except Exception`` wrappers) so the
    conflicting run refuses itself BEFORE any audit row, heartbeat beat or
    exit hook is installed: it leaves no trace but its stderr line, and no
    slot to release. The "skipped" claim state (DB unreachable) skips the
    cross-check too — admitted uncounted, healed by the watchdog.

    The refusal exits with EXIT_ALREADY_RUNNING (10), NOT 1: the work IS
    proceeding in the surviving run, so the orchestration tier runner
    counts the step OK ("deduped") instead of failing it — exit 1 made a
    re-started tier racing its own surviving children mark itself failed
    (2026-11-20 downloads_quarterly).
    """
    state, live, cap = _claim_parallel_slot(service)
    if state == "refused":
        print(
            f"[service_heartbeat] {service}: max_parallel={cap} reached "
            f"(live_parallel={live}) — already running; auto-exiting "
            "without starting",
            file=sys.stderr, flush=True,
        )
        raise SystemExit(EXIT_ALREADY_RUNNING)
    if state == "claimed":
        # The claim is owned from here on — mark it BEFORE the cross-check
        # so a conflict's release actually fires (the release pops this
        # flag and no-ops without it).
        _slots_claimed[service] = True
        conflicts = _parallel_conflicts(service)
        if conflicts:
            _release_parallel_slot(service)
            print(
                f"[service_heartbeat] {service}: parallel-services "
                f"conflict — {'; '.join(conflicts)}; auto-exiting without "
                "starting",
                file=sys.stderr, flush=True,
            )
            raise SystemExit(EXIT_ALREADY_RUNNING)


def register_service(
    service: str,
    scope: str | None = None,
    sub_scope: str | None = None,
    interval_s: float = HEARTBEAT_INTERVAL_S,
) -> HeartbeatThread:
    """Register this process under its unique service name and start the
    heartbeat. The always-on services call this from ``main()`` for the
    long-running mode only — the --once / --code dev modes exit before a
    beat would matter (and a beat from a dying one-shot would make the
    watchdog think the real service is alive). Batch pipelines register
    through DataPipeline.execute() or autoregister() instead: their exit
    lands as a normal stop record + a closed audit row (a lightweight run
    record).

    Registration first enforces the service's parallel policy
    (static JSON config — database/sql/monitoring/static/
    service_config/<dotted.service.path>.json, tree-root default.json
    fallback): the ``max_parallel`` cap by atomically claiming a
    live_parallel slot on the heartbeat row, then the
    included/excluded_parallel_services lists cross-service — a duplicate
    run auto-exits (SystemExit) instead of double-running, a
    parallel-conflicting run likewise, and the slot is released on the
    recorded exit — see _enforce_parallel_policy /
    _release_parallel_slot.

    scope/sub_scope default to the MONITORED_SERVICES registry entry; an
    unknown service must pass scope explicitly.
    """
    reg_scope, reg_sub = MONITORED_SERVICES.get(service, (scope, sub_scope))
    if reg_scope is None:
        raise ValueError(
            f"{service}: scope is required for services not in "
            "MONITORED_SERVICES"
        )
    existing = _threads.get(service)
    if existing is not None:
        # Already registered in this process (double execute()) — the
        # first registration owns the heartbeat + audit life.
        return existing
    _enforce_parallel_policy(service)
    _install_exit_hooks()

    # Open the audit row for this process life BEFORE the beat thread
    # starts (best effort — a failed insert only loses the history entry,
    # never the heartbeat).
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    _AUDIT_INSERT_SQL,
                    (service, reg_scope, reg_sub, os.getpid()),
                )
                _audit_ids[service] = cur.fetchone()[0]
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print(
            f"[service_heartbeat] {service}: audit row insert failed "
            f"(run will not be audited): {e}",
            file=sys.stderr,
            flush=True,
        )

    thread = HeartbeatThread(
        service, reg_scope, reg_sub, interval_s,
        # The code fingerprint is per LIFE: computed once here from the
        # on-disk sources this process was (about to be) loaded from,
        # then written unchanged by every beat — the stream watchdog's
        # stale-process check compares it against a FRESH disk hash per
        # scan and restarts the service when the two drift apart.
        code_fingerprint=source_fingerprint(fingerprint_roots_for(service)),
    )
    _threads[service] = thread
    # The mailbox state (Phase 4): the thread dispatches claimed
    # commands into it; the work loop reads it at its checkpoints
    # (pause_checkpoint / consume_flush).
    _command_states[service] = ServiceCommandState(service)
    thread.start()
    return thread
