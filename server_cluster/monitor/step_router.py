"""server_cluster.monitor.step_router — the chain's step execution face.

Phase 1 of the 2026-11-21 control unification plan: whoever CONFIGURES a
child also SPAWNS it. The orchestration runner (and the non-daily
backfill drive) used to submit every step into the monitor's own run
registry — an ``owns()`` bypass that parented 89 of 91 tier steps by the
monitor while belonging to other areas (the largest orphan class: after
a monitor restart nobody re-adopted the chain's children). Execution now
rides THIS module instead:

* :class:`StepRouter` — maps ``module -> (area, base_url)`` from
  ``ClusterConfig.areas[*].module_prefixes`` — the SAME ownership source
  the catalog (:mod:`server_cluster._common.catalog`) and the Express
  ``py-runner`` map mirror; no new table. Routing failure (no area owns
  the module) raises :class:`UnroutedModuleError` — there is NO
  in-process fallback, a fallback would preserve the ambiguity this
  phase removes.
* :class:`OwningStepsClient` — the HTTP face of the owning servers'
  ``/jobs`` surface: submit (``POST /jobs/run``), poll (``GET
  /jobs/status``), stop (``POST /jobs/{tag}/stop``). A step tagged by
  the runner is spawned by the OWNING server's registry (its
  ``owns()`` guard passes by construction), tracked there, and adopted
  by THAT server after ITS restart; the monitor holds only the
  route + tag. Requests ride :mod:`_common.internal_request` (the
  repo's ONE internal HTTP executor).

Uniform on purpose: a monitor-area step (``database.*`` etc.) routes to
the monitor ITSELF and submits through its own ``/jobs`` router over
loopback HTTP — one code path for every area, and the ownership
guarantee (the spawning registry's ``owns()``) holds for all of them.
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from pydantic import BaseModel, Field

from _common.internal_request import InternalRequestError, request_json

logger = logging.getLogger(__name__)

# Loopback control calls: submit/stop spawn or signal a child (fast);
# status is a registry dict read. 10s bounds all three comfortably
# (the maintainer restart's 25s covers a terminate grace; a /jobs/run
# is one Popen).
_TIMEOUT_S = 10.0


class StepRoute(BaseModel):
    """The owning server of one module — the runner's per-step record."""

    area: str = Field(description="The owning area key (downloader, "
                       "builder, analyzer, strategist, monitor).")
    service: str = Field(description="The owning server's heartbeat "
                         "service name (== its module path).")
    base_url: str = Field(description="The owning server's base url "
                          "(http://host:port).")


class UnroutedModuleError(ValueError):
    """No area in the cluster config owns the module — the step cannot
    be spawned (the runner counts it as a step failure; there is no
    in-process fallback)."""


class StepRouter:
    """``module -> StepRoute`` over ``ClusterConfig.areas[*].
    module_prefixes`` — the cluster's ONE ownership table (the config
    file keeps its ports current; the routes resolve per call site,
    so a config edit never needs a code change)."""

    def __init__(self) -> None:
        from server_cluster._common.config import ClusterConfig

        self._cluster = ClusterConfig.load()

    def route(self, module: str) -> StepRoute:
        """The owning server for ``module`` (longest-prefix wins when a
        module could match several areas' prefixes — today the prefix
        sets are disjoint, so the first match is already unique; the
        tie-break is defense, not policy)."""
        best: tuple[int, str, str, str] | None = None
        for area, cfg in self._cluster.areas.items():
            for prefix in cfg.module_prefixes:
                if not module.startswith(prefix):
                    continue
                score = len(prefix)
                if best is None or score > best[0]:
                    best = (score, area, cfg.service,
                            f"http://{cfg.host}:{cfg.port}")
        if best is None:
            raise UnroutedModuleError(
                f"module {module!r} matches no area's module_prefixes "
                f"(cluster config owns: "
                f"{ {a: c.module_prefixes for a, c in self._cluster.areas.items() if c.module_prefixes} }"
                ")")
        return StepRoute(area=best[1], service=best[2], base_url=best[3])


class OwningStepsClient:
    """Submit / poll / stop steps on the OWNING servers' ``/jobs``
    surface (the three calls the runner's wave loop needs). Every
    failure raises :class:`InternalRequestError` — the callers fold it
    into their own typed failure handling (a submit failure counts a
    failed step; an unreachable area during a poll is bounded before
    the step fails)."""

    @staticmethod
    def _encoded_tags(tags: list[str]) -> str:
        """Comma-joined query value — each tag percent-encoded so the
        charset's ``+``/``=``/``;`` survive the query round trip (the
        house tags are ``:``/``.``/``-``/``_`` today, but the jobs
        contract's charset is wider)."""
        return ",".join(urllib.parse.quote(t, safe="") for t in tags)

    def submit(self, route: StepRoute, module: str, args: list[str],
               tag: str) -> tuple[str, bool]:
        """``POST {base_url}/jobs/run`` — the OWNING server spawns (or
        dedupes) the run. Returns (tag, already_running) — the same
        contract the local registry.submit answers, now over HTTP."""
        doc = request_json(
            "POST", f"{route.base_url}/jobs/run",
            json_body={"module": module, "args": args, "tag": tag},
            timeout_s=_TIMEOUT_S)
        if not isinstance(doc, dict) or not doc.get("tag"):
            raise InternalRequestError(
                f"POST {route.base_url}/jobs/run: non-object or tagless "
                f"response: {doc!r}")
        return str(doc["tag"]), bool(doc.get("already_running"))

    def status_many(self, route: StepRoute,
                    tags: list[str]) -> dict[str, dict[str, Any]]:
        """``GET {base_url}/jobs/status?tags=...`` — one entry per
        requested tag, the registry.status_many shape verbatim."""
        doc = request_json(
            "GET",
            f"{route.base_url}/jobs/status?tags={self._encoded_tags(tags)}",
            timeout_s=_TIMEOUT_S)
        if not isinstance(doc, dict):
            raise InternalRequestError(
                f"GET {route.base_url}/jobs/status: non-object response")
        return doc.get("statuses") or {}

    def stop(self, route: StepRoute, tag: str) -> bool:
        """``POST {base_url}/jobs/{tag}/stop`` — SIGTERM → grace →
        SIGKILL by the owning server (the ladder stays the give-way
        backstop). True when a run was addressed."""
        doc = request_json(
            "POST",
            f"{route.base_url}/jobs/"
            f"{urllib.parse.quote(tag, safe='')}/stop",
            timeout_s=_TIMEOUT_S)
        if not isinstance(doc, dict):
            raise InternalRequestError(
                f"POST {route.base_url}/jobs/{tag}/stop: non-object "
                f"response")
        return bool(doc.get("stopped"))
