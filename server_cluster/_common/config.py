"""server_cluster._common.config — the cluster's typed configuration.

Pydantic models (repo convention) with the llm-area host three-layer
resolution — CODE DEFAULTS ← ``cluster_config.json`` ← CLI args — so the
checked-in JSON stays the operator-facing source of record and every
field still has a working default (a missing/malformed file must never
stop a resident server; same tolerance contract as the service_config
tree in ``_common.service_heartbeat``).

The five areas are defined wholesale by :data:`DEFAULT_AREAS` (ports
continue the resident-loopback scheme 8765–8775; module prefixes match
the pipeline-area taxonomy of ``_common/service_heartbeat.py``;
residents are the area's supervised always-on children — the downloader
supervises the five stream pollers (migrated from MONITORED_SERVICES
2026-11) and the monitor supervises the other seven cluster SERVERS
(migrated from MONITORED_SERVICES 2026-11 — the watchdog → monitor →
residents chain; monitor cannot supervise itself, so it stays the
watchdog's own charge). A ``cluster_config.json`` section MERGES over its
area's defaults field-by-field, so the JSON usually only pins ports and
overrides resident desired states; runtime desired-state drift persists
separately under the gitignored ``server_cluster/_state/`` (see
:mod:`.resident_supervisor`).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "cluster_config.json"

# Gitignored runtime-state dir (resident desired-state drift) — a source
# edit must never ship operator state, and a fresh clone must boot.
STATE_DIR = PACKAGE_DIR / "_state"

DEFAULT_HOST = "127.0.0.1"


class ResidentSpec(BaseModel):
    """One always-on child process the area server supervises: identity +
    the desired state the supervisor converges to."""

    service: str = Field(
        description="The child's dotted module path — also its heartbeat "
        "service name (the child registers itself via autoregister on "
        "start; the supervisor's liveness check is pid + service).")
    desired: Literal["running", "stopped"] = Field(
        default="running",
        description="The state the supervisor converges to; a manual stop "
        "flips this to stopped (persisted in the state file) so a stopped "
        "resident stays stopped across server restarts.")


class AreaConfig(BaseModel):
    """One area server: identity, bind, area ownership, residents, and
    the lifecycle policy knobs (the watchdog's crash-loop values as
    defaults)."""

    area: str = Field(description="The area key (downloader/builder/"
                      "analyzer/strategist/llm) — also the package name.")
    service: str = Field(description="This server's dotted module path — "
                         "the heartbeat service name (spawn contract).")
    module_prefixes: list[str] = Field(
        description="Dotted module prefixes this server accepts as jobs "
        "(the area's cmd ownership).")
    host: str = Field(default=DEFAULT_HOST,
                      description="uvicorn bind host (loopback only — "
                      "the cluster serves the local Express API).")
    port: int = Field(ge=1, le=65535)
    residents: list[ResidentSpec] = Field(
        default_factory=list,
        description="The area's supervised always-on children.")
    # --- lifecycle policy (mirrors stream_watchdog's burst guard) -------
    poll_interval_s: float = Field(
        default=5.0, ge=1.0,
        description="Registry sweep + resident supervision cadence.")
    stop_grace_s: float = Field(
        default=10.0, ge=1.0,
        description="SIGTERM → SIGKILL grace for job/resident stops (the "
        "child's own heartbeat exit hooks need the window to record).")
    crash_burst_limit: int = Field(
        default=3, ge=1,
        description="Resident restarts within the burst window before the "
        "supervisor backs off (crash-loop guard).")
    crash_burst_window_s: float = Field(default=900.0, ge=1.0)
    crash_backoff_s: float = Field(
        default=600.0, ge=1.0,
        description="How long a crash-looped resident waits before the "
        "supervisor tries again.")
    # --- registry bounds -------------------------------------------------
    finished_history_size: int = Field(
        default=256, ge=1,
        description="Bounded finished-run store (LRU; the Express API "
        "fetches results right after completion, never hours later).")
    log_tail_bytes: int = Field(
        default=262144, ge=1024,
        description="How much of a run's service log the result endpoint "
        "returns as its output tails.")
    # --- restart execution (2026-11-13 detection/execution split) --------
    restart_via_maintainer: bool = Field(
        default=False,
        description="When true, this server's resident restarts are "
        "DELEGATED over HTTP to the maintainer server (the cluster's "
        "ONE executor, POST /processes/{service}/restart) — the "
        "supervisor stays the detector (desired state, adoption, crash "
        "backoff, stale-process fingerprints) and falls back to its "
        "in-process spawn whenever the maintainer is unreachable. "
        "Set on the areas that HAVE residents (downloader, monitor).")


def _default_area(area: str, port: int, prefixes: list[str],
                  residents: Optional[list[ResidentSpec]] = None,
                  restart_via_maintainer: bool = False
                  ) -> AreaConfig:
    """One default area — the service name IS ``server_cluster.<area>``
    (the watchdog's spawn contract)."""
    return AreaConfig(
        area=area, service=f"server_cluster.{area}",
        module_prefixes=prefixes, port=port,
        residents=[r.model_copy() for r in (residents or [])],
        restart_via_maintainer=restart_via_maintainer)


# The five known areas — the checked-in registry (a cluster_config.json
# section merges over its area field-by-field; unknown sections warn and
# are ignored). The stream pollers moved here from
# _common/service_heartbeat.MONITORED_SERVICES: two-level supervision —
# the stream watchdog watches the servers, the downloader watches
# the pollers. The ``llm`` area (migrated from
# llm_agents.local_model_host 2026-11) serves its OWN model-host app
# (server_cluster/llm — chat/systemone/tokenization/models-admin/ops),
# NOT the shared jobs surface, so it owns no module prefixes and has no
# residents; its host_config.json stays the model/bind/lifecycle source
# of record (this entry is the cluster-tier port record).
STREAM_POLLERS = [
    ResidentSpec(service=f"downloads.stream.{ex}.price")
    for ex in ("sse", "szse", "bse", "csindex", "cnindex")
]

# The other seven cluster SERVERS are the monitor area's residents
# (migrated from _common/service_heartbeat.MONITORED_SERVICES 2026-11 —
# the same move the stream pollers made): the monitor server supervises
# them via its ResidentSupervisor (desired state, crash backoff,
# persisted manual stops, adoption of externally started instances).
# ``server_cluster.monitor`` is NOT in the list — a server cannot
# supervise itself, so it stays the stream watchdog's own charge (the
# chain: WSL boot → stream_watchdog → monitor → residents). The spawn
# contract is unchanged: the service name IS the ``python -m`` module.
SERVER_RESIDENTS = [
    ResidentSpec(service=f"server_cluster.{area}")
    for area in ("downloader", "builder", "analyzer", "strategist",
                 "llm", "config", "maintainer")
]

DEFAULT_AREAS: dict[str, AreaConfig] = {
    # downloader + monitor are the two areas with residents — both
    # DELEGATE their restarts to the maintainer (the cluster's ONE
    # executor, 2026-11-13 detection/execution split); the in-process
    # spawn stays as the fallback (bootstrap + maintainer outage).
    "downloader": _default_area("downloader", 8769, ["downloads."],
                                residents=STREAM_POLLERS,
                                restart_via_maintainer=True),
    "builder": _default_area("builder", 8770, ["builds."]),
    "analyzer": _default_area("analyzer", 8771, ["analysis."]),
    "strategist": _default_area("strategist", 8772,
                                ["strategy.", "live."]),
    "llm": _default_area("llm", 8767, []),
    # Ops area (2026-11): owns the always-on infrastructure modules the
    # data_viz API used to spawn locally — the shared jobs surface runs
    # database.*/llm_agents.*/test.* modules over HTTP; its own routers
    # add the fleet health check, resident aggregation, the ensure-up
    # bring-back, and the orchestration tier runner. (The daily 07:00
    # bring-up schedule moved to the maintainer area 2026-11 —
    # maintainer/bring_up.py.) Its RESIDENTS are the other seven cluster
    # SERVERS (SERVER_RESIDENTS — migrated from MONITORED_SERVICES
    # 2026-11); monitor itself stays the stream watchdog's charge.
    "monitor": _default_area("monitor", 8773,
                             ["database.", "llm_agents.", "test."],
                             residents=SERVER_RESIDENTS,
                             restart_via_maintainer=True),
    # Read-only configuration area (2026-11): serves the service catalog
    # (the service_config tree + per-area residents),
    # the orchestration step files, and the cluster topology as JSON — the
    # UI API reads configuration over HTTP instead of from disk/DB.
    # Serves its OWN lean app (no jobs surface, no residents), like llm.
    "config": _default_area("config", 8774, []),
    # Maintenance area (2026-11): the cluster's resident janitor —
    # periodic maintenance jobs on a daily schedule (03:30 Asia/Shanghai,
    # env MAINTAINER_FIRE_AT): the log cleanup (logs/ files and date dirs
    # older than one month) today, plus the daily 07:00 China-time fleet
    # bring-up (env MAINTAINER_BRING_UP_AT) migrated from the monitor
    # area — it fires the monitor's POST /residents/ensure-up and audits
    # the pass to monitor.service_audit. Serves its OWN lean app (no jobs
    # surface), like config; the API lists the job catalogue with each
    # job's last result and can fire one on demand. It is itself one of
    # SERVER_RESIDENTS — a monitor-area supervised child.
    "maintainer": _default_area("maintainer", 8775, []),
}


class ClusterConfig(BaseModel):
    """The whole cluster: one config section per area server."""

    areas: dict[str, AreaConfig] = Field(
        default_factory=lambda: {
            name: cfg.model_copy(deep=True)
            for name, cfg in DEFAULT_AREAS.items()})

    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> "ClusterConfig":
        """Defaults ← JSON file (default cluster_config.json beside this
        package; absent/malformed → pure defaults with a warning). Each
        present area section REPLACES its defaults field-by-field (a
        plain dict merge over ``model_dump``); CLI overrides are applied
        on the returned instance's chosen area by the caller
        (``__main__``)."""
        cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
        try:
            doc = json.loads(cfg_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.info("server cluster: no config file at %s — using "
                        "built-in defaults", cfg_path)
            return cls()
        except (OSError, ValueError) as exc:
            logger.warning("server cluster: config file %s unreadable "
                           "(%r) — using built-in defaults", cfg_path, exc)
            return cls()
        if not isinstance(doc, dict):
            logger.warning("server cluster: config file %s is not an "
                           "object — using built-in defaults", cfg_path)
            return cls()
        cfg = cls()
        for area, section in doc.items():
            base = cfg.areas.get(area)
            if base is None:
                logger.warning("server cluster: config section %r names "
                               "no known area — ignored", area)
                continue
            merged = {**base.model_dump(), **(section if isinstance(
                section, dict) else {})}
            try:
                cfg.areas[area] = AreaConfig.model_validate(merged)
            except Exception as exc:  # noqa: BLE001 — never block the resident
                logger.warning("server cluster: config section %r invalid "
                               "(%r) — area keeps built-in defaults",
                               area, exc)
        return cfg

    def area(self, area: str) -> AreaConfig:
        """The one area's config (KeyError on an unknown area — the
        launcher only asks for its own)."""
        return self.areas[area]
