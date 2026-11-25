"""server_cluster.maintainer.app — the maintenance server's lean app.

One lean FastAPI (the config-area own-app shape): liveness, the job
catalogue with each job's last result, and a run-on-demand trigger for
a single job or the shared schedule. ONE scheduler daemon thread owns
every fire — jobs declare their own ``fire_at`` group (03:30 shared
maintenance, 07:00 fleet bring-up + morning data audit, 16:00 nightly
pipeline chain); the API shares the same job objects, so an on-demand
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
from server_cluster.maintainer.backfill_latest import LatestSessionBackfillJob
from server_cluster.maintainer.bring_up import FleetBringUpJob
from server_cluster.maintainer.daily_check import DailyDataCheckJob
from server_cluster.maintainer.jobs import (DbTableAuditArchiveJob,
                                            JobResult, LogCleanupJob,
                                            StatsDbBackupJob)
from server_cluster.maintainer.non_daily import (NonDailyBackfillJob,
                                                 NonDailyBackfillRunRequest)
from server_cluster.maintainer.pipeline import (NightlyPipelineJob,
                                                PipelineLastPass,
                                                PipelineRunRequest)
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
        self.pipeline = NightlyPipelineJob()
        self.non_daily_backfill = NonDailyBackfillJob()
        self.daily_data_check = DailyDataCheckJob()
        # The backfill job shares the pipeline instance — its chain
        # trigger rides the ONE chain lock, never a second one.
        self.backfill_latest = LatestSessionBackfillJob(self.pipeline)
        self.jobs: list = [LogCleanupJob(), StatsDbBackupJob(),
                           DbTableAuditArchiveJob(), FleetBringUpJob(),
                           self.daily_data_check, self.backfill_latest,
                           self.pipeline, self.non_daily_backfill]
        self.scheduler = MaintenanceScheduler(self.jobs)
        self.app = self._build_app()

    def _build_app(self) -> FastAPI:
        app = FastAPI(
            title=f"server-cluster-{self.config.area}",
            description="Resident maintenance API: the cluster's "
            "schedules — daily maintenance jobs (03:30), the fleet "
            "bring-up (07:00), the morning data audit (07:00: the UI "
            "API health + every daily_data_checks item's latest-"
            "loaded-vs-frontier check, stale items to the Cautions "
            "tab), the missing-session backfill (07:15: purge a "
            "partial latest-CLOSED-session's rows per daily-bar family "
            "and run downloads→builds→analysis through the pipeline "
            "job to fill it), the nightly pipeline chain (16:00 "
            "China time on A-share trading days: monitoring → "
            "downloads → builds → analysis → strategy driven through "
            "the monitor area's orchestration runner; the recovery "
            "surface GET /pipeline/last-pass + POST /pipeline/finish "
            "covers failed or interrupted passes), and the Saturday "
            "non-daily backfill (05:00 China time: the monitor area's "
            "/non-daily completeness checks + weekend re-runs of "
            "missing monthly/quarterly/semiannual tasks, weekly retry "
            "until loaded) — plus run-on-demand "
            "over the same job objects, and, since 2026-11-13, the "
            "cluster's ONE resident-process EXECUTOR (the "
            "monitor/downloader detectors POST their restarts here "
            "instead of spawning in-process).",
            version="1.0.0",
        )

        @app.on_event("startup")
        async def _start_threads() -> None:
            self.scheduler.start()
            # An interrupted nightly chain (state file from today)
            # resumes on a daemon thread — the in-flight tier, if any,
            # is adopted first, never raced.
            self.pipeline.resume_if_incomplete()

        @app.on_event("shutdown")
        async def _stop_threads() -> None:
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
        # minutes-to-hours-long blocking work (a whole-tree mirror
        # walk, a full tier chain), and an async def would run it on
        # the event loop, wedging every other endpoint until the pass
        # ends. FastAPI runs sync endpoints on the threadpool, so the
        # API stays live meanwhile.
        @app.post("/jobs/{name}/run", response_model=JobResult)
        def run_job(name: str,
                    req: Optional[PipelineRunRequest] = None) -> JobResult:
            job = next((j for j in self.jobs if j.name == name), None)
            if job is None:
                raise HTTPException(
                    status_code=404, detail=f"no job named {name!r} — "
                    f"known: {[j.name for j in self.jobs]}")
            if isinstance(job, (NightlyPipelineJob, NonDailyBackfillJob)):
                return job.run(req)
            return job.run()

        @app.post("/jobs/run-all", response_model=list[JobResult])
        def run_all() -> list:
            return self.scheduler.run_all()

        # The nightly chain's recovery surface — the 21:00 check (or
        # an operator) reads the last pass's record and finishes /
        # reruns what remains. GET is instant; finish is a sync ``def``
        # like the run endpoints (it IS a chain pass, hours-long).
        @app.get("/pipeline/last-pass", response_model=PipelineLastPass)
        async def pipeline_last_pass() -> PipelineLastPass:
            return self.pipeline.last_pass()

        @app.post("/pipeline/finish", response_model=JobResult)
        def pipeline_finish() -> JobResult:
            return self.pipeline.finish_incomplete()

        return app


def create_maintainer_app(config: AreaConfig,
                          cluster: Optional[ClusterConfig] = None) -> FastAPI:
    """The ``__main__`` entry — the config-area factory shape."""
    return MaintenanceApp(config, cluster).app
