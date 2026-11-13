"""server_cluster.llm.app — the app factory (the official
``main.py`` slot): the ONE place that knows the whole surface.

``create_app`` builds the FastAPI, installs the config/manager on
``app.state`` (the routers resolve them per request via
``routers.deps`` — never through closures), installs the OpenAI error
handler, includes every ``routers/`` module, and wires the lifespan
(idle-offload watchdog start + the startup warm-up pass). Composition
root only — no route logic lives here."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from . import SERVICE
from .config import HostConfig
from .manager import ModelManager
from .routers import ROUTER_MODULES
from .routers.errors import install_validation_handler

logger = logging.getLogger(__name__)


def create_app(config: Optional[HostConfig] = None,
               manager: Optional[ModelManager] = None) -> FastAPI:
    """Build the FastAPI app around one config + manager (both default-
    constructed when omitted — the TestClient/app-factory form; the
    resident path passes the manager ``LocalModelHost`` already owns so
    its lifecycle outlives test doubles)."""
    config = config or HostConfig.load()
    manager = manager or ModelManager(config)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        manager.start()
        if config.warmup_on_start:
            manager.warmup_async()
        try:
            yield
        finally:
            manager.stop()

    app = FastAPI(
        title="local-model-host",
        description="Resident host for the repo's local AI models "
                    "(OpenAI chat-completions surface + tokenization + "
                    "load/offload management).",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.manager = manager
    app.state.service_name = SERVICE

    install_validation_handler(app)
    for module in ROUTER_MODULES:
        app.include_router(module.router)
    return app
