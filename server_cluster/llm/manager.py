"""server_cluster.llm.manager — the model registry + lifecycle.

The migrated + generalized management layer: what used to be the word
segmenter's private lifecycle (lazy load, one-hour idle offload to CPU
with weights kept in RAM, transparent GPU rehydrate, warm-up) is here
ONE registry driving EVERY model uniformly:

* ``models`` — the configured backends (:meth:`build_backend` factory);
  construction is light, weights load lazily,
* :meth:`load` / :meth:`offload` — the admin API's engine (idempotent,
  lock-serialized per model; sync — the server routes run them via
  ``asyncio.to_thread`` so the event loop never blocks),
* :meth:`start` — ONE idle-offload watchdog daemon thread sweeping
  every ``check_s`` (the segmenter's own cadence:
  ``max(1, min(30, idle_offload_s/10))``) and offloading models whose
  ``last_use`` is older than their policy age,
* :meth:`warmup_all` — the startup warm-up pass (background thread):
  each ``warmup``-enabled model loads onto the GPU and runs a tiny
  inference, so first real requests never pay cold-start. A failed
  warm-up never takes the host down — the model's status records the
  error and the admin API can retry the load.

Request counters (requests / errors / last status per model) live here
too — :meth:`stats_snapshot` feeds GET /stats.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from .backends import ModelBackend, build_backend
from .config import HostConfig, ModelBackendSpec
from .data_definitions.admin import ModelStatus

logger = logging.getLogger(__name__)


class UnknownModelError(KeyError):
    """Chat/admin request named a model_id outside the registry — the
    server maps this to the OpenAI 404 model_not_found envelope."""


class ModelManager:
    """The host's model registry + lifecycle machinery (see module
    docstring)."""

    def __init__(self, config: HostConfig) -> None:
        self._config = config
        self._models: dict[str, ModelBackend] = {}
        for spec in config.enabled_models():
            self._models[spec.model_id] = build_backend(
                spec, config.idle_offload_s)
        # Request counters per model (stamped by the server routes).
        self._stats: dict[str, dict] = {
            mid: {"requests": 0, "errors": 0} for mid in self._models}
        self._halt = threading.Event()
        self._watchdog: threading.Thread | None = None
        self._warmup_thread: threading.Thread | None = None
        self._started_at = time.monotonic()

    # ------------------------------------------------------------ registry
    def get(self, model_id: str) -> ModelBackend:
        backend = self._models.get(model_id)
        if backend is None:
            raise UnknownModelError(model_id)
        return backend

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._models

    def ids(self) -> list[str]:
        return list(self._models)

    def spec_of(self, model_id: str) -> ModelBackendSpec:
        return self.get(model_id).spec

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Start the idle-offload watchdog (idempotent)."""
        if self._watchdog is not None:
            return
        min_idle = min(
            (b.idle_offload_s for b in self._models.values()),
            default=self._config.idle_offload_s)
        check_s = max(1.0, min(30.0, min_idle / 10.0))
        self._watchdog = threading.Thread(
            target=self._idle_watchdog_loop, args=(check_s,),
            name="lmh-idle-watchdog", daemon=True)
        self._watchdog.start()
        logger.info("local model host: manager started (idle watchdog "
                    "every %.0fs; %d models: %s)", check_s,
                    len(self._models), ", ".join(self._models))

    def stop(self) -> None:
        self._halt.set()

    def warmup_async(self) -> None:
        """Run the startup warm-up pass in a background thread (models
        load onto the GPU while the server is already serving)."""
        if self._warmup_thread is not None:
            return

        def run() -> None:
            for model_id, backend in self._models.items():
                if not backend.spec.warmup:
                    continue
                t0 = time.perf_counter()
                try:
                    backend.load()
                    logger.info(
                        "local model host: warm-up %s done in %.1fs",
                        model_id, time.perf_counter() - t0)
                except Exception as exc:  # noqa: BLE001 — never take the host down
                    logger.warning(
                        "local model host: warm-up %s failed (%r) — the "
                        "model stays available for a retry via POST "
                        "/v1/models/%s/load", model_id, exc, model_id)

        self._warmup_thread = threading.Thread(
            target=run, name="lmh-warmup", daemon=True)
        self._warmup_thread.start()

    def load(self, model_id: str) -> ModelStatus:
        backend = self.get(model_id)
        backend.load()
        return backend.status()

    def offload(self, model_id: str) -> ModelStatus:
        backend = self.get(model_id)
        backend.offload()
        return backend.status()

    # -------------------------------------------------------------- status
    def status_all(self) -> list[ModelStatus]:
        return [b.status() for b in self._models.values()]

    def status_of(self, model_id: str) -> ModelStatus:
        return self.get(model_id).status()

    def note_request(self, model_id: str, *, error: bool = False) -> None:
        counters = self._stats.get(model_id)
        if counters is None:
            counters = self._stats[model_id] = {
                "requests": 0, "errors": 0}
        counters["requests"] += 1
        if error:
            counters["errors"] += 1

    def stats_snapshot(self) -> dict[str, dict]:
        """Counters + the backends' own status extras per model."""
        out: dict[str, dict] = {}
        for model_id, backend in self._models.items():
            entry = dict(self._stats.get(model_id, {}))
            status = backend.status()
            entry["state"] = status.state
            entry["idle_for_s"] = status.idle_for_s
            out[model_id] = entry
        return out

    @property
    def uptime_s(self) -> float:
        return time.monotonic() - self._started_at

    # ------------------------------------------------------------- watchdog
    def _idle_watchdog_loop(self, check_s: float) -> None:
        """The one-hour policy, per model: sweep; offload whatever sat
        idle past its own ``idle_offload_s``."""
        while not self._halt.wait(check_s):
            for model_id, backend in self._models.items():
                if backend.state != "loaded":
                    continue
                idle = backend.idle_for_s
                if idle is None or idle < backend.idle_offload_s:
                    continue
                try:
                    backend.offload()
                except Exception:  # noqa: BLE001 — logged, retried next sweep
                    logger.exception(
                        "local model host: idle offload failed for %s",
                        model_id)
