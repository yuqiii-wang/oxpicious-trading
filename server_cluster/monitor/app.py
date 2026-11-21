"""server_cluster.monitor.app — the ops area's composition root.

The shared jobs surface (``app_factory.create_app`` — jobs, processes,
health routers + the resident supervisor) PLUS this package's fleet,
orchestration and non-daily routers. (The daily 07:00 bring-up scheduler
lived here until 2026-11 — it moved to the maintainer area,
``maintainer/bring_up.py``; its fire comes back as a POST to
``/residents/ensure-up``.) No route logic lives here."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI

from server_cluster._common.config import AreaConfig
from . import arrangement, fleet, non_daily, orchestration
from .arrangement import OrchestrationArranger
from .fleet import FleetOps
from .non_daily import NonDailyOps
from .orchestration import OrchestrationRunner

logger = logging.getLogger(__name__)


def create_monitor_app(config: Optional[AreaConfig] = None) -> FastAPI:
    """Build the ops app: the shared surface + this area's routers."""
    from server_cluster._common.app_factory import create_app

    if config is None:
        from server_cluster._common.config import DEFAULT_AREAS

        config = DEFAULT_AREAS["monitor"].model_copy(deep=True)

    app = create_app(config)

    app.state.fleet = FleetOps(
        config, app.state.processes, app.state.supervisor)
    app.state.orchestration = OrchestrationRunner(app.state.registry)
    app.state.arrangement = OrchestrationArranger()
    app.state.non_daily = NonDailyOps(
        app.state.registry, app.state.orchestration)

    app.include_router(fleet.router)
    app.include_router(orchestration.router)
    app.include_router(arrangement.router)
    app.include_router(non_daily.router)
    return app
