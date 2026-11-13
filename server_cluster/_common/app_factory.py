"""server_cluster._common.app_factory — the app factory: the ONE place
that knows the whole surface.

``create_app`` builds the FastAPI around one area's collaborators
(process manager, run registry, resident supervisor), installs them on
``app.state`` (the routers resolve them per request via
``routers.deps`` — never through closures), installs the validation
handler, includes every ``routers/`` module, and wires the lifespan:
adoption of orphaned children, the resident supervisor's start (its
first pass IS the boot-time auto-start), and the registry's reaping
sweep. Composition root only — no route logic lives here."""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from .config import AreaConfig
from .process_manager import ProcessManager
from .resident_supervisor import ResidentSupervisor
from .routers import ROUTER_MODULES
from .routers.errors import install_validation_handler
from .run_registry import RunRegistry

logger = logging.getLogger(__name__)


def create_app(config: Optional[AreaConfig] = None) -> FastAPI:
    """Build the FastAPI app for ONE area (the TestClient/app-factory
    form defaults to that area's built-in config; the resident path
    passes the loaded config so the registry/supervisor outlive test
    doubles)."""
    from .config import DEFAULT_AREAS

    if config is None:
        config = next(iter(DEFAULT_AREAS.values())).model_copy(deep=True)

    processes = ProcessManager(config)
    registry = RunRegistry(config, processes)
    # The detection/execution split (2026-11-13): an area flagged
    # restart_via_maintainer hands its resident restarts to the
    # maintainer server over HTTP and keeps only the DETECTION here
    # (desired state, adoption, backoff, staleness) — None on any
    # failure falls back to the in-process spawn below.
    delegate = None
    if config.restart_via_maintainer:
        from .maintainer_client import restart_via_maintainer

        delegate = restart_via_maintainer
    supervisor = ResidentSupervisor(config, processes,
                                    restart_delegate=delegate)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        registry.adopt()
        supervisor.start()
        sweep_stop = threading.Event()
        sweeper = threading.Thread(
            target=_sweep_loop, name=f"cluster-{config.area}-sweep",
            args=(registry, sweep_stop, config.poll_interval_s),
            daemon=True)
        sweeper.start()
        try:
            yield
        finally:
            sweep_stop.set()
            sweeper.join(timeout=config.poll_interval_s + 1.0)
            supervisor.stop()

    app = FastAPI(
        title=f"server-cluster-{config.area}",
        description=f"Resident API entry over the {config.area} area's "
                    "pipeline cmds (jobs + resident-process control), "
                    "serving the data_viz API instead of direct WSL "
                    "spawns.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.processes = processes
    app.state.registry = registry
    app.state.supervisor = supervisor

    install_validation_handler(app)
    for module in ROUTER_MODULES:
        app.include_router(module.router)
    return app


def _sweep_loop(registry: RunRegistry, stop: threading.Event,
                interval_s: float) -> None:
    """The registry's reaping sweep — own spawns exit through their
    Popen handles, adopted runs through /proc liveness; finished runs
    move to the bounded store. A failed pass is logged and retried."""
    while not stop.wait(interval_s):
        try:
            registry.sweep()
        except Exception:  # noqa: BLE001 — a sweep must never take the
            logger.exception("registry sweep pass failed")  # noqa: BLE001
