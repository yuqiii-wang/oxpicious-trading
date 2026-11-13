"""server_cluster._common.resident_supervisor — the area's always-on
children, converging to their desired state.

Two-level supervision: ``database.stream_watchdog`` supervises the four
cluster SERVERS; each server supervises its own area's residents (for
the downloader: the five stream pollers that moved out of
``MONITORED_SERVICES``). The loop every ``poll_interval_s``:

1. A resident with a dead/unknown pid is ADOPTED when it is actually
   running (``/proc`` scan — an operator may have started it via
   ``scripts/stream_always.sh``); an adopted resident is left running,
   whatever its desired state says (desired ``stopped`` only means
   "don't START it", never "kill what the operator started").
2. A resident that is NOT running and DESIRES ``running`` is started —
   unless the crash-loop guard holds it back: more than
   ``crash_burst_limit`` starts within ``crash_burst_window_s`` back
   the resident off for ``crash_backoff_s`` (the stream watchdog's
   burst-guard values by default).

A manual stop (``POST /processes/{service}/stop``) flips the desired
state to ``stopped`` and PERSISTS it under the gitignored
``server_cluster/_state/`` — the whole point of the takeover: unlike
under the central watchdog, a deliberately stopped resident stays
stopped across restarts. ``start``/``restart`` flip it back and act
immediately (explicit operator intent bypasses the backoff).

2026-11-13 — DETECTION / EXECUTION SPLIT: this class is the DETECTOR
only. When a ``restart_delegate`` is wired (the monitor + downloader,
``AreaConfig.restart_via_maintainer``), a start/restart rides
``POST /processes/{service}/restart`` on the MAINTAINER server (the
cluster's ONE resident-process executor,
``server_cluster/maintainer/processes.py``) and the returned pid is
tracked like an own spawn; ``None`` (maintainer down / refused) falls
back to the in-process spawn — the bootstrap path (at monitor boot the
maintainer is down) AND the outage path (a maintainer death must never
leave residents unsupervised). A STALE resident — its heartbeat
``code_fingerprint`` no longer matches the on-disk sources — is
detected here too (slow sub-pass, the stream watchdog's debounced
rule) and restarted through the same delegate.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from .config import STATE_DIR, AreaConfig, ResidentSpec
from .process_manager import ProcessManager, find_running_module

logger = logging.getLogger(__name__)

# The stale-process sub-pass cadence: every Nth converge pass (5s poll →
# ~60s), the same interval database/stream_watchdog.py scans at — a
# content hash over the resident's source roots is milliseconds-cheap
# but pointless per-5s.
_STALE_CHECK_EVERY_PASSES = 12

_FINGERPRINT_SQL = """
SELECT code_fingerprint
FROM monitor.service_heartbeat
WHERE service = %s
"""


class ResidentStatus(BaseModel):
    """One resident's row — the /processes and /stats payload."""

    service: str
    desired: str
    state: str = Field(description="running | down (the supervisor will "
                       "start it) | backoff (crash-looped, waiting) | "
                       "stopped (desired stopped, not running)")
    pid: int | None = None
    adopted: bool = Field(default=False, description="Running under an "
                          "externally-started pid this server adopted.")
    restarts_recent: int = Field(default=0, description="Starts within "
                                 "the burst window.")
    backoff_until: float | None = None


class ResidentSupervisor:
    """Desired-state manager for ONE area's residents (a daemon thread;
    an area with no residents simply converges over an empty table)."""

    def __init__(
        self, config: AreaConfig, processes: ProcessManager,
        restart_delegate: Callable[[str], int | None] | None = None,
    ) -> None:
        self._config = config
        self._processes = processes
        # The execution delegate (2026-11-13 split): None = spawn
        # in-process; wired = POST the restart to the maintainer (the
        # cluster's ONE executor) and fall back in-process on None.
        self._restart_delegate = restart_delegate
        self._specs: dict[str, ResidentSpec] = {
            r.service: r for r in config.residents}
        # runtime state
        self._pids: dict[str, int] = {}
        self._adopted: set[str] = set()
        self._restarts: dict[str, list[float]] = {}
        self._backoff_until: dict[str, float] = {}
        self._desired: dict[str, str] = {
            s: r.desired for s, r in self._specs.items()}
        self._load_desired_drift()
        # the stale-process sub-pass's debounce (service → drifted disk
        # hash; the restart fires when the SAME hash is seen again)
        self._stale_seen: dict[str, str] = {}
        self._pass_count = 0
        # thread lifecycle
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # lifecycle (lifespan wires start/stop)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the convergence loop + do the FIRST pass synchronously
        (the boot-time auto-start of desired-running residents)."""
        self._converge()
        self._thread = threading.Thread(
            target=self._loop, name=f"cluster-{self._config.area}-residents",
            daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the loop to end (children are session-detached and
        intentionally survive the server)."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._config.poll_interval_s + 1.0)

    def _loop(self) -> None:
        while not self._stop_event.wait(self._config.poll_interval_s):
            try:
                self._converge()
            except Exception:  # noqa: BLE001 — a supervision pass must
                logger.exception(  # noqa: BLE001 — never take the server down
                    "%s: resident supervision pass failed",
                    self._config.service)

    # ------------------------------------------------------------------
    # convergence
    # ------------------------------------------------------------------

    def _converge(self) -> None:
        now = time.time()
        for service, spec in self._specs.items():
            pid = self._pids.get(service)
            if pid is not None and not self._processes.alive(pid, service):
                logger.info("%s: resident pid %d is gone", service, pid)
                pid = None
            if pid is None:
                # adopt an externally-started instance before spawning
                external = find_running_module(service)
                if external is not None:
                    logger.info("%s: adopted externally-started pid %d",
                                service, external)
                    self._pids[service] = external
                    self._adopted.add(service)
                    continue
            if pid is not None:
                continue  # running (ours or adopted) — nothing to do
            if self._desired.get(service, spec.desired) != "running":
                continue  # desired stopped — stays down
            until = self._backoff_until.get(service)
            if until is not None and now < until:
                continue  # crash-looped — wait it out
            self._start_resident(service)
        # The stale-process sub-pass rides the same loop on its slower
        # cadence (a dead resident is the fast pass's concern; a resident
        # running OLD code is this one's).
        self._pass_count += 1
        if self._pass_count % _STALE_CHECK_EVERY_PASSES == 0:
            try:
                self._check_staleness()
            except Exception:  # noqa: BLE001 — same never-take-the-
                logger.exception(  # noqa: BLE001 — server-down contract
                    "%s: staleness pass failed", self._config.service)

    def _start_resident(self, service: str) -> bool:
        """Start one resident (burst-guarded): through the MAINTAINER
        when a delegate is wired (the cluster's ONE executor — the call
        returns the fresh pid; termination of a stale instance happened
        there), else in-process. True when started."""
        now = time.time()
        window = now - self._config.crash_burst_window_s
        recent = [t for t in self._restarts.get(service, []) if t > window]
        if len(recent) >= self._config.crash_burst_limit:
            self._backoff_until[service] = now + self._config.crash_backoff_s
            self._restarts[service] = recent
            logger.warning(
                "%s: crash-loop guard — %d restarts within %.0fs, backing "
                "off %.0fs", service, len(recent),
                self._config.crash_burst_window_s,
                self._config.crash_backoff_s)
            return False
        pid: int | None = None
        if self._restart_delegate is not None:
            delegated = self._restart_delegate(service)
            if delegated is not None:
                pid = delegated
                logger.info("%s: started by the MAINTAINER as pid %d "
                            "(delegated restart)", service, pid)
        if pid is None:
            pid, _log_path = self._processes.spawn(service, [])
        self._pids[service] = pid
        self._adopted.discard(service)
        self._restarts.setdefault(service, []).append(now)
        self._backoff_until.pop(service, None)
        return True

    # ------------------------------------------------------------------
    # stale residents — running code that predates the sources
    # ------------------------------------------------------------------

    def _check_staleness(self) -> None:
        """One staleness pass over the RUNNING residents (the stream
        watchdog's debounced rule, resident-tier): a heartbeat
        ``code_fingerprint`` that no longer matches a fresh disk hash
        means the process executes startup-time code while the sources
        moved on. First mismatch ALERTS; when the same drifted hash is
        still there on the next pass, the resident restarts — through
        the delegate (the maintainer) when wired, else in-process.
        Fail-soft end to end: a DB outage, an unregistered resident or
        a fingerprinting error only skips the check."""
        from _common.service_heartbeat import (
            fingerprint_roots_for, source_fingerprint, stale_check_for,
        )
        for service, pid in list(self._pids.items()):
            if service not in self._specs or pid is None:
                continue
            if not self._processes.alive(pid, service):
                continue  # dead — the liveness pass owns that case
            if self._desired.get(service, "running") != "running":
                continue  # desired stopped — its life is Ending anyway
            if not stale_check_for(service):
                continue
            row_fp = self._heartbeat_fingerprint(service)
            if not row_fp or row_fp == "unknown":
                continue
            disk_fp = source_fingerprint(fingerprint_roots_for(service))
            if disk_fp == "unknown":
                continue
            if disk_fp == row_fp:
                self._stale_seen.pop(service, None)
                continue
            if self._stale_seen.get(service) != disk_fp:
                self._stale_seen[service] = disk_fp
                logger.warning(
                    "[ALERT] %s: code changed under the resident process "
                    "(pid %d) — restarting next staleness pass if the "
                    "sources stay unchanged", service, pid)
                continue
            self._stale_seen.pop(service, None)
            logger.warning(
                "[ALERT] %s: stale resident (pid %d) — code changed since "
                "start and stayed changed; restarting through the "
                "maintainer", service, pid)
            # NOT self.restart(): that is the explicit-operator-intent
            # path and clears the crash-loop state — a flapping source
            # file must stay bounded by the burst guard. The staleness
            # restart keeps the guard: terminate the known pid (instant,
            # the child records its own exit) and let _start_resident
            # run the delegated/burst-guarded start.
            if self._processes.alive(pid, service):
                self._processes.terminate(pid, service)
            self._pids.pop(service, None)
            self._adopted.discard(service)
            self._start_resident(service)

    def _heartbeat_fingerprint(self, service: str) -> str | None:
        """The resident's own per-life code fingerprint from its
        heartbeat row (NULL = pre-column life — uncheckable)."""
        from _common.db_commons import get_db_connection

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(_FINGERPRINT_SQL, (service,))
                row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # operator control (the /processes router)
    # ------------------------------------------------------------------

    def snapshot(self) -> list[ResidentStatus]:
        """The residents' current rows (a liveness probe per resident)."""
        now = time.time()
        rows: list[ResidentStatus] = []
        for service, spec in self._specs.items():
            pid = self._pids.get(service)
            alive = pid is not None and self._processes.alive(pid, service)
            desired = self._desired.get(service, spec.desired)
            if alive:
                state = "running"
            elif desired == "stopped":
                state = "stopped"
            elif now < self._backoff_until.get(service, 0.0):
                state = "backoff"
            else:
                state = "down"
            rows.append(ResidentStatus(
                service=service, desired=desired, state=state,
                pid=pid if alive else None,
                adopted=service in self._adopted and alive,
                restarts_recent=len([
                    t for t in self._restarts.get(service, [])
                    if t > now - self._config.crash_burst_window_s]),
                backoff_until=self._backoff_until.get(service),
            ))
        return rows

    def set_desired(self, service: str, desired: str) -> bool:
        """Flip a resident's desired state, act NOW (start/stop
        immediately), and persist the drift. Unknown service → False."""
        if service not in self._specs:
            return False
        self._desired[service] = desired
        self._save_desired_drift()
        pid = self._pids.get(service)
        alive = pid is not None and self._processes.alive(pid, service)
        if desired == "stopped":
            if alive:
                self._processes.terminate(pid, service)
            self._pids.pop(service, None)
            self._adopted.discard(service)
        else:  # running — explicit intent bypasses the crash backoff
            # BOTH halves of the guard: popping _backoff_until alone was
            # not enough — _start_resident re-trips the burst guard from
            # the recent-restart timestamps and silently no-ops (the
            # /start endpoint answered ok=true while nothing started).
            # An explicit operator start clears the whole crash-loop
            # state: the human said START.
            self._backoff_until.pop(service, None)
            self._restarts.pop(service, None)
            if not alive:
                self._start_resident(service)
        return True

    def restart(self, service: str) -> bool:
        """Stop (if running) then start one resident, clearing its
        crash-loop state (explicit operator intent — see set_desired).
        Unknown service → False."""
        if service not in self._specs:
            return False
        pid = self._pids.get(service)
        if pid is not None and self._processes.alive(pid, service):
            self._processes.terminate(pid, service)
        self._pids.pop(service, None)
        self._adopted.discard(service)
        self._desired[service] = "running"
        self._backoff_until.pop(service, None)
        self._restarts.pop(service, None)
        self._save_desired_drift()
        self._start_resident(service)
        return True

    # ------------------------------------------------------------------
    # desired-state drift (the gitignored _state file)
    # ------------------------------------------------------------------

    def _state_path(self) -> Path:
        return STATE_DIR / f"{self._config.area}_residents.json"

    def _load_desired_drift(self) -> None:
        """Config defaults ← the persisted drift (a missing/corrupt file
        simply keeps the config defaults)."""
        try:
            doc = json.loads(self._state_path().read_text(
                encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning("%s: residents state file unreadable (%r) — "
                           "config defaults hold", self._config.area, exc)
            return
        desired = doc.get("desired", {}) if isinstance(doc, dict) else {}
        for service, state in desired.items():
            if service in self._specs and state in ("running", "stopped"):
                self._desired[service] = state

    def _save_desired_drift(self) -> None:
        """Persist only the entries that DIVERGE from the config default
        (the file stays small and reviewable)."""
        diverging = {
            service: state
            for service, state in self._desired.items()
            if state != self._specs[service].desired
        }
        path = self._state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if diverging:
                path.write_text(
                    json.dumps({"desired": diverging}, indent=2),
                    encoding="utf-8")
            elif path.exists():
                path.unlink()  # back to all-defaults — no drift to keep
        except OSError as exc:
            logger.warning("%s: could not persist residents state (%r)",
                           self._config.area, exc)
