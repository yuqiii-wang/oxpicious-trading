"""server_cluster._common.run_registry — the cluster's tag→run table.

The durable-shape successor of the Express API's in-memory
``runningByTag`` map (``data_viz/api/services/py-runner.service.ts``):
process-id-tags stay the cross-boundary contract the frontend
reconstructs (``analysis-run:<module>:<sec_type>:<code>``,
``singleton-run:...``, ...) — the owning cluster server now holds the
truth, so dedupe and status polling survive Express AND cluster
restarts.

* ACTIVE runs — this process's (or adopted) children, keyed by tag.
* FINISHED runs — a bounded LRU store (the Express API fetches the
  result right after completion; a run's captured output IS its
  service-log tail, see ``ProcessManager.tail``).
* ADOPTION — at STARTUP the server re-attaches orphaned children, and
  on a periodic sweep too (Phase 1b of the 2026-11-21 control
  unification): open ``monitor.service_audit`` rows under the area's
  prefixes whose pid still runs the module (``pid_alive_for_service``).
  The startup pass covers this server's own restart; the PERIODIC pass
  covers children this server never saw — residue of a restart of a
  DIFFERENT server (pre-Phase-1 cross-area parenting), an operator-
  started run, or a re-parented chain child — without waiting for this
  server's own restart. ADOPTED runs have no ``Popen`` handle (the
  child was spawned by a dead process), so their exit is liveness-
  detected with an unknown exit code — the child's own heartbeat audit
  row is the durable record then. A pid already tracked among the
  active runs is never double-adopted under the ``adopted:`` tag.

The adoption SELECT is a module constant beside its logic, following
the monitoring-internals precedent (``database/stream_watchdog.py``);
the DB access is best-effort — a monitoring outage must never stop the
server from serving.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path

from pydantic import BaseModel, Field

from .config import AreaConfig
from .process_manager import UNKNOWN_EXIT_CODE, ProcessManager

logger = logging.getLogger(__name__)

# The periodic re-adopt cadence: every Nth registry sweep (5s poll →
# ~60s). The query is one bounded SELECT + one pid-liveness probe per
# candidate — cheap at this cadence.
_READOPT_EVERY_PASSES = 12

# Bound on one adoption pass's rows (more than this many open area
# audits is pathological; the pass skips to keep the sweep cheap).
_OPEN_AUDITS_LIMIT = 200

_OPEN_AREA_AUDITS_SQL = """
SELECT a.id, a.service, a.pid, a.started_at
FROM monitor.service_audit a
WHERE a.ended_at IS NULL
  AND ({prefix_clauses})
LIMIT {limit}
"""


class RunEntry(BaseModel):
    """One run of one area cmd — the registry's record and the API's
    status/result payload base."""

    tag: str = Field(description="The process-id-tag (the dedupe/status "
                     "identity the Express API and the frontend share).")
    module: str
    args: list[str] = Field(default_factory=list)
    pid: int | None = Field(default=None, description="The child's pid "
                            "once spawned (None only while the spawn "
                            "itself failed).")
    started_at: float
    ended_at: float | None = None
    exit_code: int | None = Field(default=None, description="None = not "
                                  "finished (or an adopted run's "
                                  "unknowable code).")
    log_path: str = Field(default="", description="The child's service "
                          "log — the result endpoint's output source.")
    adopted: bool = Field(default=False, description="True when re-"
                          "attached from an open service_audit row at "
                          "startup (no Popen handle).")
    stopped_by_api: bool = Field(default=False, description="True when a "
                                 "POST /jobs stop addressed this run.")

    def running(self) -> bool:
        return self.ended_at is None


class RunRegistry:
    """Thread-safe tag→run registry for ONE area server."""

    def __init__(self, config: AreaConfig, processes: ProcessManager) -> None:
        self._config = config
        self._processes = processes
        self._lock = threading.Lock()
        self._active: dict[str, RunEntry] = {}
        self._finished: OrderedDict[str, RunEntry] = OrderedDict()
        self._sweep_passes = 0

    # ------------------------------------------------------------------
    # submit / stop
    # ------------------------------------------------------------------

    def submit(self, module: str, args: list[str],
               tag: str) -> tuple[RunEntry, bool]:
        """Spawn (or dedupe) one run. Returns (entry, already_running) —
        ``already_running`` mirrors the Express contract: the same tag
        in flight means NOTHING was spawned."""
        with self._lock:
            existing = self._active.get(tag)
            if existing is not None:
                return existing.model_copy(), True
        pid, log_path = self._processes.spawn(module, args)
        entry = RunEntry(tag=tag, module=module, args=list(args), pid=pid,
                         started_at=time.time(), log_path=str(log_path))
        with self._lock:
            # A raced submit could have taken the tag while we spawned
            # (two concurrent POSTs, both past the first check) — keep
            # the FIRST entry, and leave the duplicate child running:
            # the child's own max_parallel heartbeat claim is the
            # backstop that refuses it.
            first = self._active.get(tag)
            if first is not None:
                return first.model_copy(), True
            self._active[tag] = entry
        return entry.model_copy(), False

    def stop(self, tag: str) -> bool:
        """SIGTERM → grace → SIGKILL an active run (best effort). True
        when a run was addressed."""
        entry = self._active_by_tag(tag)
        if entry is None or entry.pid is None:
            return False
        self._processes.terminate(entry.pid, entry.module)
        with self._lock:
            entry.stopped_by_api = True
        return True

    # ------------------------------------------------------------------
    # sweep — reap exits, bound the finished store
    # ------------------------------------------------------------------

    def sweep(self) -> int:
        """One reaping pass over the active runs: own spawns exit through
        their Popen handle, adopted runs through /proc liveness (unknown
        code). Returns how many runs finished. Called from the lifespan
        sweep thread every ``poll_interval_s``; every
        ``_READOPT_EVERY_PASSES``-th pass also carries the continuous
        adoption sweep (Phase 1b)."""
        with self._lock:
            entries = list(self._active.values())
        finished = 0
        for entry in entries:
            if entry.pid is None:
                continue
            if entry.adopted:
                if not self._processes.alive(entry.pid, entry.module):
                    self._finish(entry.tag, UNKNOWN_EXIT_CODE)
                    finished += 1
            else:
                code = self._processes.poll(entry.pid)
                if code is not None:
                    self._finish(entry.tag, code)
                    finished += 1
        self._sweep_passes += 1
        if self._sweep_passes % _READOPT_EVERY_PASSES == 0:
            self.readopt()
        return finished

    def _finish(self, tag: str, exit_code: int) -> None:
        with self._lock:
            entry = self._active.pop(tag, None)
            if entry is None:
                return
            entry.ended_at = time.time()
            entry.exit_code = exit_code
            self._finished[tag] = entry
            while len(self._finished) > self._config.finished_history_size:
                self._finished.popitem(last=False)
        logger.info("run finished: %s (%s) exit %s",
                    tag, entry.module, exit_code)

    # ------------------------------------------------------------------
    # reads — status / result / stats
    # ------------------------------------------------------------------

    def _active_by_tag(self, tag: str) -> RunEntry | None:
        with self._lock:
            entry = self._active.get(tag)
            return entry.model_copy() if entry else None

    def _finished_by_tag(self, tag: str) -> RunEntry | None:
        with self._lock:
            entry = self._finished.get(tag)
            return entry.model_copy() if entry else None

    def status_many(self, tags: list[str]) -> dict[str, dict]:
        """The Express ``getPythonProcessStatus`` shape, enriched: one
        entry per requested tag; unknown tags answer ``running: false``."""
        out: dict[str, dict] = {}
        for tag in tags:
            entry = self._active_by_tag(tag) or self._finished_by_tag(tag)
            if entry is None:
                out[tag] = {"running": False, "known": False}
            else:
                out[tag] = {
                    "running": entry.running(),
                    "known": True,
                    "module": entry.module,
                    "pid": entry.pid,
                    "started_at": entry.started_at,
                    "ended_at": entry.ended_at,
                    "exit_code": entry.exit_code,
                    "adopted": entry.adopted,
                }
        return out

    def result(self, tag: str) -> dict | None:
        """A run's outcome: status fields + the service-log tail as the
        captured stdout/stderr (one file, both streams — the Express
        ``RunScriptResult`` contract maps ``stdout``/``stderr`` onto the
        same tail). None for an unknown tag."""
        entry = self._active_by_tag(tag) or self._finished_by_tag(tag)
        if entry is None:
            return None
        tail = self._processes.tail(Path(entry.log_path))
        return {
            "tag": entry.tag,
            "module": entry.module,
            "pid": entry.pid,
            "running": entry.running(),
            "exit_code": entry.exit_code,
            "started_at": entry.started_at,
            "ended_at": entry.ended_at,
            "adopted": entry.adopted,
            "log_path": entry.log_path,
            "stdout": tail,
            "stderr": tail,
        }

    def counts(self) -> dict[str, int]:
        """The /stats snapshot numbers."""
        with self._lock:
            return {
                "running": len(self._active),
                "finished_stored": len(self._finished),
                "adopted": sum(1 for e in self._active.values() if e.adopted),
            }

    # ------------------------------------------------------------------
    # startup adoption — re-attach orphaned children
    # ------------------------------------------------------------------

    def adopt(self) -> int:
        """Re-attach running children left by a previous life of this
        server (the STARTUP pass — the lifespan calls it before the
        sweep thread starts). Returns how many runs were adopted. Best
        effort — a DB outage degrades to zero adoptions (dedupe then
        rides the children's own heartbeat parallel claims)."""
        return self._adopt_rows(self._open_area_audit_rows())

    def readopt(self) -> int:
        """The CONTINUOUS adoption sweep (Phase 1b — every Nth registry
        sweep): re-attach running children this registry never tracked.
        Same query and liveness rule as :meth:`adopt`, but a pid already
        tracked among the active runs is never double-adopted (our own
        spawns and the already-adopted ones stay under their tags).
        Best effort by the same contract."""
        return self._adopt_rows(self._open_area_audit_rows())

    def _adopt_rows(self, rows: list[tuple]) -> int:
        """The shared adoption body: one RunEntry per live candidate,
        keyed ``adopted:{service}:{pid}``. A pid already tracked among
        the active runs is skipped (the periodic pass would otherwise
        re-adopt our own spawns under a second tag), and so is this
        area's own RESIDENT population — the supervisor tracks those
        lives (a resident in the registry would double-book its every
        restart as a finished run and inflate /stats)."""
        resident_services = {r.service for r in self._config.residents}
        with self._lock:
            tracked_pids = {e.pid for e in self._active.values()
                            if e.pid is not None}
        adopted = 0
        for _audit_id, service, pid, started_at in rows:
            if pid is None or pid in tracked_pids:
                continue
            if service in resident_services:
                continue  # the supervisor owns that life, not the registry
            from _common.service_heartbeat import pid_alive_for_service

            if not pid_alive_for_service(pid, service):
                continue
            tag = f"adopted:{service}:{pid}"
            entry = RunEntry(
                tag=tag, module=service, args=[], pid=pid,
                started_at=started_at if isinstance(started_at, (int, float))
                else time.time(),
                log_path=str(_guess_log_path(service)),
                adopted=True)
            with self._lock:
                if tag in self._active or any(
                        e.pid == pid for e in self._active.values()):
                    continue
                self._active[tag] = entry
                tracked_pids.add(pid)
            adopted += 1
            logger.info("adopted %s (pid %d) from an open audit row",
                        service, pid)
        if adopted:
            logger.info("adoption: %d orphaned run(s) re-attached", adopted)
        return adopted

    def _open_area_audit_rows(self) -> list[tuple]:
        """Open audit rows under this area's prefixes (one bounded round
        trip; the prefix LIKE list is built from the config's own
        prefixes)."""
        prefixes = self._config.module_prefixes
        clauses = " OR ".join(["a.service LIKE %s"] * len(prefixes))
        sql = _OPEN_AREA_AUDITS_SQL.format(prefix_clauses=clauses,
                                           limit=_OPEN_AUDITS_LIMIT)
        try:
            from _common.db_commons import get_db_connection

            conn = get_db_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, [f"{p}%" for p in prefixes])
                    return list(cur.fetchall())
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 — best effort by contract
            logger.warning("adoption query failed (%r) — starting with "
                           "no adopted runs", exc)
            return []


def _guess_log_path(module: str) -> Path:
    """The adopted child's TODAY log (its start may predate today — the
    tail then comes back empty, which is honest: the durable record is
    the child's audit row)."""
    from .process_manager import _log_path_for

    return _log_path_for(module)
