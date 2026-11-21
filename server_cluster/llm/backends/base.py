"""server_cluster.llm.backends.base — the managed-model ABC.

Every backend wraps ONE local model behind the same lifecycle the word
segmenter has always run (lazy load, transparent GPU rehydrate,
idle-offload to CPU with weights kept in RAM) — the machinery migrated
out of ``_common.tokenization.chinese_word_segmenter`` and generalized,
now TWO idle tiers:

* tier 1: after ``idle_offload_s`` idle seconds the weights move GPU →
  CPU (RAM keeps them, CUDA cache emptied) — state ``offloaded``;
* tier 2: after a further ``idle_unload_s`` idle seconds on CPU the
  weights drop from RAM entirely — state ``unloaded``; the next
  ``ensure_loaded()`` cold-loads from disk.

* one ``threading.Lock`` per model serializes load / inference /
  offload / unload (a single GPU; inference methods acquire it
  themselves via :meth:`run_locked`),
* the state machine ``not_loaded → loading → loaded|error`` plus
  ``offloaded`` (weights in RAM, off the GPU) and ``unloaded`` (no
  weights in memory) drives
  :class:`~.data_definitions.admin.ModelStatus`,
* :meth:`load` / :meth:`offload` / :meth:`unload` / :meth:`warmup` are
  the admin-API entry points (idempotent), :meth:`ensure_loaded` is the
  inference path's internal (lock-held) loader,
* ``last_use`` stamping (:meth:`touch`) feeds the manager's idle
  watchdog.

Subclasses implement :meth:`_load` (weights onto the device),
:meth:`_offload` (weights to CPU + free the CUDA cache), :meth:`_unload`
(weights dropped from RAM) and optionally :meth:`_warmup` (a tiny
inference so the first real request is fast). Backends without weights
(the dummy simulation) override ``_load/_offload/_unload`` as no-ops and
are always ``loaded``.
"""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod

from ..config import ModelBackendSpec
from ..data_definitions.admin import ModelStatus

logger = logging.getLogger(__name__)


class ModelBackend(ABC):
    """One managed local model (see module docstring)."""

    def __init__(self, spec: ModelBackendSpec,
                 default_idle_offload_s: float,
                 default_idle_unload_s: float) -> None:
        self.spec = spec
        self.model_id = spec.model_id
        self.idle_offload_s = (default_idle_offload_s
                               if spec.idle_offload_s is None
                               else float(spec.idle_offload_s))
        self.idle_unload_s = (default_idle_unload_s
                              if spec.idle_unload_s is None
                              else float(spec.idle_unload_s))
        self.lock = threading.Lock()

        self._state = "not_loaded"
        self._error: str | None = None
        self._last_use = 0.0
        self._offloaded_at: float | None = None
        self._loaded_once = False
        self._warmed = False

    # ------------------------------------------------------------ abstract
    @abstractmethod
    def _load(self) -> None:
        """Load the weights onto the device. Lock held by caller."""

    @abstractmethod
    def _offload(self) -> None:
        """Move the weights to CPU and free the CUDA cache. Lock held."""

    @abstractmethod
    def _unload(self) -> None:
        """Drop the weights from RAM entirely. Lock held by caller."""

    def _warmup(self) -> None:
        """One tiny inference after load (default: nothing to warm)."""

    # ------------------------------------------------------------ lifecycle
    @property
    def state(self) -> str:
        return self._state

    @property
    def idle_for_s(self) -> float | None:
        """Seconds since the last use — None before the first use."""
        if self._last_use <= 0.0:
            return None
        return max(0.0, time.monotonic() - self._last_use)

    @property
    def offloaded_for_s(self) -> float | None:
        """Seconds since the GPU→CPU offload (the tier-2 age) — None
        unless currently offloaded."""
        if self._offloaded_at is None:
            return None
        return max(0.0, time.monotonic() - self._offloaded_at)

    def touch(self) -> None:
        """Stamp `last use = now` — every inference call booksends this
        so the idle watchdog's countdown restarts."""
        self._last_use = time.monotonic()

    def ensure_loaded(self) -> None:
        """Load lazily / rehydrate after an idle offload. Caller holds
        :attr:`lock`."""
        if self._state == "loaded":
            return
        if self._state == "loading":
            raise RuntimeError(
                f"{self.model_id}: a load is already in progress")
        self._state = "loading"
        t0 = time.perf_counter()
        try:
            self._load()
        except Exception as exc:
            self._state = "error"
            self._error = repr(exc)
            logger.exception("local model host: %s load failed", self.model_id)
            raise
        self._state = "loaded"
        self._error = None
        self._loaded_once = True
        self._offloaded_at = None
        self.touch()
        logger.info("local model host: %s ready in %.1fs", self.model_id,
                    time.perf_counter() - t0)

    def load(self) -> None:
        """Admin/warm-up load: ensure the weights sit on the device."""
        with self.lock:
            self.ensure_loaded()
            if not self._warmed:
                try:
                    self._warmup()
                except Exception as exc:  # noqa: BLE001 — warm-up is best effort
                    logger.warning("local model host: %s warm-up failed "
                                   "(%r) — serving anyway", self.model_id, exc)
                self._warmed = True

    def offload(self) -> None:
        """Idle-offload tier 1: weights to CPU (RAM keeps them), CUDA
        cache emptied; the next ensure_loaded() rehydrates. Idempotent."""
        with self.lock:
            if self._state != "loaded":
                return
            t0 = time.perf_counter()
            self._offload()
            self._state = "offloaded"
            self._offloaded_at = time.monotonic()
            logger.info(
                "local model host: %s offloaded to CPU after %.0fs idle "
                "(%.1fs, weights kept in RAM)", self.model_id,
                self.idle_for_s or 0.0, time.perf_counter() - t0)

    def unload(self) -> None:
        """Idle-unload tier 2: weights dropped from RAM entirely; the
        next ensure_loaded() cold-loads from disk. Only acts from the
        ``offloaded`` state (unload a loaded model by offloading
        first). Idempotent."""
        with self.lock:
            if self._state != "offloaded":
                return
            cpu_idle = self.offloaded_for_s or 0.0
            t0 = time.perf_counter()
            self._unload()
            self._state = "unloaded"
            self._offloaded_at = None
            logger.info(
                "local model host: %s unloaded from CPU after %.0fs "
                "further idle (%.1fs, weights dropped — next use "
                "cold-loads from disk)", self.model_id, cpu_idle,
                time.perf_counter() - t0)

    # -------------------------------------------------------------- inference
    def run_locked(self, fn):
        """Run ``fn()`` under the model lock with the weights ensured on
        the device — the inference path's serialization point."""
        with self.lock:
            self.ensure_loaded()
            try:
                return fn()
            finally:
                self.touch()

    def note_error(self, exc: BaseException) -> None:
        """Record an inference failure on the status (the load state
        itself stays untouched — a generate() error is not a load
        error)."""
        self._error = repr(exc)

    # --------------------------------------------------------------- status
    def status(self) -> ModelStatus:
        return ModelStatus(
            model_id=self.model_id,
            kind=self.spec.kind,
            state=self._state,
            device=self.spec.device if self._loaded_once else None,
            dtype=self.spec.dtype if self._loaded_once else None,
            idle_for_s=self.idle_for_s,
            last_use_epoch=(time.time() - self.idle_for_s
                            if self.idle_for_s is not None else None),
            error=self._error,
            detail=self._status_detail(),
        )

    def _status_detail(self) -> dict:
        """Backend-specific status extras (path, VRAM, ...)."""
        detail: dict = {}
        if self.spec.path is not None:
            detail["path"] = self.spec.path
        return detail
