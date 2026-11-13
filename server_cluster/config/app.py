"""server_cluster.config.app — the read-only configuration server.

One lean FastAPI (the llm-area own-app shape): routes serve the
REPO's configuration sources as JSON. Read-only by contract — nothing
here writes a file or a DB row; config edits stay in the git sources
and the service_config seed files. Handlers import their sources
LAZILY (service_heartbeat pulls db_commons → pandas) so the pre_check
import stays light.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from server_cluster._common.config import (
    DEFAULT_CONFIG_PATH, AreaConfig, ClusterConfig,
)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_CONFIG_DIR = _REPO_ROOT / "database" / "sql" / "monitoring" / \
    "static" / "service_config"
ORCHESTRATION_DIR = _REPO_ROOT / "scripts" / "orchestration"

_STARTED_AT = time.time()


class ConfigHealthResponse(BaseModel):
    service: str
    area: str
    pid: int
    uptime_s: float


class CatalogService(BaseModel):
    """One service's catalog row: the descriptor JSON plus its registry
    scope (the Service Monitor catalog's source of record)."""

    service: str
    scope: str | None = None
    sub_scope: str | None = None
    descriptor: dict = Field(default_factory=dict,
                             description="The service_config JSON (empty "
                             "when the service has no descriptor file).")


class CatalogResponse(BaseModel):
    services: list[CatalogService]
    descriptor_root: str


class ClusterArea(BaseModel):
    """One area's topology row (the py-runner port map + the UI
    catalog's supervisedBy source)."""

    area: str
    service: str
    host: str
    port: int
    module_prefixes: list[str]
    residents: list[str]


class ClusterResponse(BaseModel):
    areas: list[ClusterArea]
    source: str = Field(description="The cluster_config.json path the "
                        "ports were merged from (empty = built-in "
                        "defaults).")


def create_config_app(config: AreaConfig,
                      cluster: Optional[ClusterConfig] = None) -> FastAPI:
    app = FastAPI(
        title=f"server-cluster-{config.area}",
        description="Resident READ-ONLY configuration API: service "
        "catalog, orchestration step files, and cluster topology as "
        "JSON — the data_viz API reads configuration over HTTP instead "
        "of from disk/DB.",
        version="1.0.0",
    )
    app.state.config = config
    app.state.cluster = cluster or ClusterConfig.load()

    # ------------------------------------------------------------------
    # liveness
    # ------------------------------------------------------------------

    @app.get("/health", response_model=ConfigHealthResponse)
    async def health() -> ConfigHealthResponse:
        return ConfigHealthResponse(
            service=app.state.config.service, area=app.state.config.area,
            pid=os.getpid(), uptime_s=time.time() - _STARTED_AT)

    @app.get("/stats")
    async def stats() -> dict:
        health_row = await health()
        return {**health_row.model_dump(), "role": "read-only-config"}

    # ------------------------------------------------------------------
    # catalog
    # ------------------------------------------------------------------

    @app.get("/catalog", response_model=CatalogResponse)
    async def catalog() -> CatalogResponse:
        from _common.service_heartbeat import MONITORED_SERVICES

        descriptors: dict[str, dict] = {}
        if SERVICE_CONFIG_DIR.is_dir():
            for path in sorted(SERVICE_CONFIG_DIR.rglob("*.json")):
                try:
                    descriptors[path.stem] = json.loads(
                        path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    logger.warning("service_config %s unreadable: %s",
                                   path, exc)
        services = []
        for service, (scope, sub_scope) in MONITORED_SERVICES.items():
            services.append(CatalogService(
                service=service, scope=scope, sub_scope=sub_scope,
                descriptor=descriptors.get(service, {})))
        # Batch services that only have a descriptor file (they register
        # ad-hoc with explicit scopes) belong in the catalog too.
        for service, desc in descriptors.items():
            if service not in MONITORED_SERVICES:
                services.append(CatalogService(
                    service=service,
                    scope=desc.get("scope"), sub_scope=desc.get("sub_scope"),
                    descriptor=desc))
        services.sort(key=lambda s: s.service)
        return CatalogResponse(services=services,
                               descriptor_root=str(SERVICE_CONFIG_DIR))

    # ------------------------------------------------------------------
    # orchestration
    # ------------------------------------------------------------------

    @app.get("/orchestration", response_model=list[str])
    async def orchestration_files() -> list[str]:
        if not ORCHESTRATION_DIR.is_dir():
            return []
        return sorted(p.stem for p in ORCHESTRATION_DIR.glob("*.json"))

    @app.get("/orchestration/{file}")
    async def orchestration_file(file: str) -> dict:
        if not file or "/" in file or "\\" in file or ".." in file:
            raise HTTPException(
                status_code=422, detail=f"orchestration file {file!r} "
                "must be a bare name under scripts/orchestration/")
        path = ORCHESTRATION_DIR / f"{file}.json"
        if not path.is_file():
            raise HTTPException(
                status_code=404, detail=f"no orchestration file at {path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"orchestration file {path} unreadable: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # cluster topology
    # ------------------------------------------------------------------

    @app.get("/cluster", response_model=ClusterResponse)
    async def cluster_topology() -> ClusterResponse:
        areas = []
        for name, cfg in sorted(app.state.cluster.areas.items()):
            areas.append(ClusterArea(
                area=name, service=cfg.service, host=cfg.host, port=cfg.port,
                module_prefixes=list(cfg.module_prefixes),
                residents=[r.service for r in cfg.residents]))
        source = str(DEFAULT_CONFIG_PATH) if DEFAULT_CONFIG_PATH.is_file() \
            else ""
        return ClusterResponse(areas=areas, source=source)

    return app
