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
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

from _common.db_commons import get_db_connection

# ---------------------------------------------------------------------------
# Registry — unique service name -> (scope, sub_scope)
# ---------------------------------------------------------------------------
# The always-on services the stream watchdog supervises. scope is the
# top-level pipeline area (downloads / builds / analysis / strategy / live /
# database); sub_scope the level below it. Batch pipelines register ad-hoc
# with explicit scope args instead of being listed here.
WATCHDOG_SERVICE = "database.stream_watchdog"

MONITORED_SERVICES: dict[str, tuple[str, str | None]] = {
    "downloads.stream.sse.price": ("downloads", "stream"),
    "downloads.stream.szse.price": ("downloads", "stream"),
    "downloads.stream.bse.price": ("downloads", "stream"),
    "downloads.stream.csindex.price": ("downloads", "stream"),
    "downloads.stream.cnindex.price": ("downloads", "stream"),
    # DB health sampler (database/db_health.py) — always-on like the
    # pollers; the watchdog restarts it when its beat goes stale. Grouped
    # under the stream sub-scope (the always-on services) rather than a
    # monitoring one.
    "database.db_health": ("database", "stream"),
    WATCHDOG_SERVICE: ("database", "monitoring"),
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
# life after restart).
_BEAT_SQL = f"""
INSERT INTO {HEARTBEAT_TABLE}
    (service, scope, sub_scope, pid, started_at, beat_at,
     stop_reason, is_normal_exit, stopped_at)
VALUES (%s, %s, %s, %s, now(), now(), NULL, NULL, NULL)
ON CONFLICT (service) DO UPDATE
SET scope = EXCLUDED.scope,
    sub_scope = EXCLUDED.sub_scope,
    pid = EXCLUDED.pid,
    started_at = EXCLUDED.started_at,
    beat_at = now(),
    stop_reason = NULL,
    is_normal_exit = NULL,
    stopped_at = NULL
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
    pid: int | None = None,
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


def service_from_module_path(module: str) -> tuple[str, str, str | None]:
    """Dotted module path → (service, scope, sub_scope).

    ``builds.stock.__main__`` → ("builds.stock", "builds", "stock");
    ``database.monitoring``   → ("database.monitoring", "database",
                                 "monitoring"). scope is the top-level
    pipeline area (first segment), sub_scope the second — the same
    taxonomy the MONITORED_SERVICES registry uses.
    """
    parts = module.split(".")
    if parts and parts[-1] == "__main__":
        parts = parts[:-1]
    if not parts or not all(parts):
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
    ``database/monitoring.py`` → database.monitoring).

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

    Failures are rate-limited to one stderr line per 30 minutes — a DB
    outage must not flood the service's nohup log; the watchdog notices
    the missing beats on its own.
    """

    def __init__(
        self, service: str, scope: str, sub_scope: str | None,
        interval_s: float = HEARTBEAT_INTERVAL_S,
    ):
        super().__init__(name=f"heartbeat-{service}", daemon=True)
        self._service = service
        self._scope = scope
        self._sub_scope = sub_scope
        self._interval_s = interval_s
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
        while not self._halt.is_set():
            if conn is None:
                try:
                    conn = get_db_connection()
                except Exception as e:  # noqa: BLE001 — never kill the service
                    self._warn(f"connect failed, retrying next tick: {e}")
                    self._halt.wait(self._interval_s)
                    continue
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        _BEAT_SQL,
                        (self._service, self._scope, self._sub_scope, os.getpid()),
                    )
            except Exception as e:  # noqa: BLE001
                self._warn(f"beat failed, reconnecting: {e}")
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = None
            self._halt.wait(self._interval_s)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        self._halt.set()


# ---------------------------------------------------------------------------
# Exit recording — stop_reason / is_normal_exit / stopped_at
# ---------------------------------------------------------------------------

_threads: dict[str, HeartbeatThread] = {}
_exit_info: dict[str, tuple[str, bool]] = {}
_audit_ids: dict[str, int] = {}
_slots_claimed: dict[str, bool] = {}  # service → live_parallel slot taken
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
    reason, normal = _exit_info.pop(service, ("process exit", True))
    thread.stop()
    thread.join(timeout=3.0)  # no in-flight beat can clear the stop row
    # Scope from the thread's OWN registration — the registry only knows
    # the always-on services; ad-hoc/batch services would else land as
    # 'unknown' on the no-row-yet INSERT path.
    reg_scope, reg_sub = thread._scope, thread._sub_scope
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
# max_parallel / live_parallel — the concurrency guard
# ---------------------------------------------------------------------------
# The CAP is static JSON config (database/sql/monitoring/static/
# service_config/ — a per-service DIRECTORY TREE like
# external_request_config/: the dotted service name is the path, each file
# one object {service, max_parallel, freq, schedule, notes, source_ref,
# updated_at}; the tree-root default.json is the FALLBACK carrying the
# repo-wide cap), read at registration; the COUNTER (live_parallel) lives on
# the service's monitor.service_heartbeat row — the current-state row the
# claim's conditional UPDATE serializes on. Registration claims a slot
# past the cap and auto-exits the newcomer; a recorded exit releases it;
# the stream watchdog resets the counter from pid liveness when a process
# is detected down without an exit record (a dead process cannot release
# its own slot).

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

_ROW_PID_SQL = f"SELECT pid FROM {HEARTBEAT_TABLE} WHERE service = %s"


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

    Assumption shared with the watchdog's reset: an open audit life is a
    faithful liveness record (a registration whose audit INSERT failed
    would be counted dead — DB hiccups at registration only).
    """
    cur.execute(_OPEN_LIVES_PIDS_SQL, (service,))
    pids = {r[0] for r in cur.fetchall()}
    cur.execute(_ROW_PID_SQL, (service,))
    row = cur.fetchone()
    if row is not None and row[0] is not None:
        pids.add(row[0])
    alive = [p for p in pids if pid_alive_for_service(p, service)]
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


def _enforce_max_parallel(service: str) -> None:
    """Exit when the service's ``max_parallel`` is already reached.

    The check IS the claim (_claim_parallel_slot). Cap reached →
    SystemExit (BaseException — deliberately NOT swallowed by the
    best-effort ``except Exception`` wrappers) so the duplicate refuses
    itself BEFORE any audit row, heartbeat beat or exit hook is installed:
    it leaves no trace but its stderr line, and no slot to release.
    """
    state, live, cap = _claim_parallel_slot(service)
    if state == "refused":
        raise SystemExit(
            f"[service_heartbeat] {service}: max_parallel={cap} reached "
            f"(live_parallel={live}) — already running; auto-exiting "
            "without starting"
        )
    if state == "claimed":
        _slots_claimed[service] = True


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

    Registration first enforces the service's ``max_parallel``
    (static JSON config — database/sql/monitoring/static/
    service_config/<dotted.service.path>.json, tree-root default.json
    fallback, default 1) by atomically claiming a
    live_parallel slot on the heartbeat row: a duplicate run auto-exits
    (SystemExit) instead of double-running, and the slot is released on
    the recorded exit — see _enforce_max_parallel /
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
    _enforce_max_parallel(service)
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

    thread = HeartbeatThread(service, reg_scope, reg_sub, interval_s)
    _threads[service] = thread
    thread.start()
    return thread
