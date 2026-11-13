"""server_cluster.maintainer.app — the maintenance server's lean app.

One lean FastAPI (the config-area own-app shape): liveness, the job
catalogue with each job's last result, and a run-on-demand trigger for
a single job or the whole schedule. The scheduler daemon thread owns
the daily fire; the API shares the same job objects, so an on-demand
run refreshes ``last_result`` everywhere.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from server_cluster._common.config import AreaConfig, ClusterConfig
from server_cluster._common.process_manager import ProcessManager
from server_cluster.maintainer.bring_up import DailyBringUp, FleetBringUpJob
from server_cluster.maintainer.jobs import (JobResult, LogCleanupJob,
                                            StatsDbBackupJob)
from server_cluster.maintainer.processes import build_router
from server_cluster.maintainer.scheduler import MaintenanceScheduler

logger = logging.getLogger(__name__)

_STARTED_AT = time.time()


class MaintainerHealthResponse(BaseModel):
    service: str
    area: str
    pid: int
    uptime_s: float


class JobStatus(BaseModel):
    name: str
    description: str
    last_result: Optional[JobResult] = None


class JobListResponse(BaseModel):
    jobs: list[JobStatus]


class MaintenanceApp:
    """Composition for the maintainer area: the job catalogue + the
    scheduler + the wired FastAPI (one instance per server)."""

    def __init__(self, config: AreaConfig,
                 cluster: Optional[ClusterConfig] = None) -> None:
        self.config = config
        self.processes = ProcessManager(config)
        self.bring_up = DailyBringUp()
        self.jobs: list = [LogCleanupJob(), StatsDbBackupJob(),
                           self.bring_up.job]
        self.scheduler = MaintenanceScheduler(self.jobs)
        self.app = self._build_app()

    def _build_app(self) -> FastAPI:
        app = FastAPI(
            title=f"server-cluster-{self.config.area}",
            description="Resident maintenance API: periodic cluster "
            "maintenance jobs (log cleanup today) on a daily schedule, "
            "plus run-on-demand over the same job objects — and, since "
            "2026-11-13, the cluster's ONE resident-process EXECUTOR "
            "(the monitor/downloader detectors POST their restarts "
            "here instead of spawning in-process).",
            version="1.0.0",
        )

        @app.on_event("startup")
        async def _start_threads() -> None:
            self.scheduler.start()
            self.bring_up.start()

        @app.on_event("shutdown")
        async def _stop_threads() -> None:
            self.bring_up.stop()
            self.scheduler.stop()

        app.include_router(build_router(self.processes))

        @app.get("/health", response_model=MaintainerHealthResponse)
        async def health() -> MaintainerHealthResponse:
            return MaintainerHealthResponse(
                service=self.config.service, area=self.config.area,
                pid=os.getpid(), uptime_s=time.time() - _STARTED_AT)

        @app.get("/stats")
        async def stats() -> dict:
            health_row = await health()
            return {**health_row.model_dump(), "role": "maintenance",
                    "jobs": [j.name for j in self.jobs]}

        @app.get("/jobs", response_model=JobListResponse)
        async def jobs() -> JobListResponse:
            return JobListResponse(jobs=[JobStatus(
                name=j.name, description=j.description,
                last_result=j.last_result) for j in self.jobs])

        # The run endpoints are sync ``def`` on purpose: a job pass is
        # minutes-long blocking work (a whole-tree mirror walk), and an
        # async def would run it on the event loop, wedging every other
        # endpoint until the pass ends. FastAPI runs sync endpoints on
        # the threadpool, so the API stays live meanwhile.
        @app.post("/jobs/{name}/run", response_model=JobResult)
        def run_job(name: str) -> JobResult:
            job = next((j for j in self.jobs if j.name == name), None)
            if job is None:
                raise HTTPException(
                    status_code=404, detail=f"no job named {name!r} — "
                    f"known: {[j.name for j in self.jobs]}")
            return job.run()

        @app.post("/jobs/run-all", response_model=list[JobResult])
        def run_all() -> list:
            return self.scheduler.run_all()

        return app


def create_maintainer_app(config: AreaConfig,
                          cluster: Optional[ClusterConfig] = None) -> FastAPI:
    """The ``__main__`` entry — the config-area factory shape."""
    return MaintenanceApp(config, cluster).app
