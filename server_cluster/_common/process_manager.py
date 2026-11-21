"""server_cluster._common.process_manager — spawn/stop/observe the area's
child cmds.

The cluster's ONE process primitive, shaped exactly like
``database/stream_watchdog.py::_spawn``: children start detached
(``start_new_session=True`` — they survive this server's death, the
two-level supervision contract), with stdout/stderr appended to the
day's service log ``logs/<date>/<module>.log`` — the SAME file the
child's own ``_common.log_setup`` handler writes and the UI's log
tailing endpoint (``GET /api/monitor/stream/runs/log``) reads. NO
pipes: a pipe would tie a detached child's writes to this process's
lifetime (SIGPIPE on server death).

Own ``Popen`` handles are kept to REAP exits (no zombies — the
registry's sweep polls them); adopted children (running processes found
at startup via ``monitor.service_audit``) have no handle, so their exit
is detected by ``/proc`` liveness and their exit code stays unknown —
the child's own heartbeat audit row is the durable record in that case.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import AreaConfig

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOG_ROOT = _REPO_ROOT / "logs"

_UNKNOWN_EXIT_CODE = -1


class ProcessManager:
    """The area's child-process table: spawn, stop, liveness, log tails.

    One per server; shared by the run registry (jobs) and the resident
    supervisor (always-on children) — both store pids, this class owns
    HOW processes start and die."""

    def __init__(self, config: AreaConfig) -> None:
        self._config = config
        # pid → live Popen handle (OUR spawns only; adopted children have
        # no handle) — polled by the registry sweep to reap exits.
        self._handles: dict[int, subprocess.Popen] = {}

    # ------------------------------------------------------------------
    # spawn / stop
    # ------------------------------------------------------------------

    def spawn(self, module: str, args: list[str]) -> tuple[int, Path]:
        """Start ``python -u -m <module> <args...>`` detached; return
        (pid, log_path). Output appends to the day's service log — the
        ``stream_watchdog._spawn`` contract verbatim."""
        log_path = _log_path_for(module)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(log_path, "ab")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-u", "-m", module, *args],
                stdout=log_f,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                cwd=_REPO_ROOT,
            )
        finally:
            log_f.close()
        self._handles[proc.pid] = proc
        logger.info("spawned %s %s as pid %d (log %s)",
                    module, " ".join(args), proc.pid, log_path)
        return proc.pid, log_path

    def poll(self, pid: int) -> int | None:
        """The exit code of a FINISHED own spawn (reaping its handle),
        None while running or for a pid we hold no handle for (adopted
        children — liveness goes through :meth:`alive` instead)."""
        proc = self._handles.pop(pid, None)
        if proc is None:
            return None
        code = proc.poll()
        if code is None:
            # still running — put the handle back
            self._handles[pid] = proc
        return code

    def alive(self, pid: int, service: str) -> bool:
        """/proc liveness narrowed to the service (pid + cmdline must run
        the module) — ``service_heartbeat.pid_alive_for_service``. Lazy
        import: that module transitively pulls pandas via db_commons and
        this class must stay importable before any heavy dep."""
        from _common.service_heartbeat import pid_alive_for_service

        return pid_alive_for_service(pid, service)

    def terminate(
            self, pid: int, service: str,
            grace_s: float | None = None) -> bool:
        """Stop a child: SIGTERM (the heartbeat's handler records the
        stop), wait out the grace window, SIGKILL a survivor. True when
        the pid was addressed at all. Best effort throughout — a stop
        must never take the SERVER down."""
        if grace_s is None:
            grace_s = self._config.stop_grace_s
        addressed = False
        # PLANNED stop: mark before the signal so the child's heartbeat
        # SIGTERM handler records the stop WITHOUT the duplicate
        # "stopped abnormally" caution — this stop is intentional and
        # already logged here / by the supervisor that asked for it.
        try:
            from _common.service_heartbeat import mark_planned_stop
            mark_planned_stop(pid)
        except Exception:  # noqa: BLE001 — best effort; the stop proceeds
            pass
        try:
            os.kill(pid, signal.SIGTERM)
            addressed = True
            logger.info("SIGTERM → %s (pid %d), grace %.0fs",
                        service, pid, grace_s)
        except ProcessLookupError:
            return False
        except OSError as exc:
            logger.warning("%s (pid %d): SIGTERM failed: %r",
                           service, pid, exc)
            return addressed
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            if not self.alive(pid, service):
                return True
            time.sleep(0.2)
        if self.alive(pid, service):
            logger.warning("%s (pid %d): survived SIGTERM — SIGKILL",
                           service, pid)
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError as exc:
                logger.warning("%s (pid %d): SIGKILL failed: %r",
                               service, pid, exc)
        return True

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------

    def tail(self, log_path: Path, n_bytes: int | None = None) -> str:
        """The log's last ``n_bytes`` (default: the config's tail bound)
        as text — a job's captured output IS its service-log tail (the
        file the child writes and the spawner redirects into)."""
        n_bytes = n_bytes or self._config.log_tail_bytes
        try:
            size = log_path.stat().st_size
            with open(log_path, "rb") as f:
                if size > n_bytes:
                    f.seek(size - n_bytes)
                data = f.read(n_bytes)
            return data.decode(errors="replace")
        except OSError:
            return ""


def _log_path_for(module: str) -> Path:
    """``logs/<YYYY-MM-DD>/<module>.log`` — the house one-file-per-
    (date, service) contract."""
    return _LOG_ROOT / datetime.now().strftime("%Y-%m-%d") / f"{module}.log"


def find_running_module(module: str) -> int | None:
    """PID of a running ``python -m <module>`` process, if /proc is
    available (same /proc scan as ``database/stream_watchdog.py``'s
    adopt pass — re-declared here because the cluster imports no
    supervision code from ``database.*``). Used to adopt externally
    started residents."""
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


UNKNOWN_EXIT_CODE = _UNKNOWN_EXIT_CODE
