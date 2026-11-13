"""server_cluster.llm.server — the resident host shell.

A thin composition root (the app factory and every route moved to
:mod:`.app` / :mod:`.routers`): this class owns ONE config, ONE model
manager and the resulting app — the object ``__main__`` builds and
hands to uvicorn, and the object probes/tests construct to point a
TestClient at.

Run: ``python -m server_cluster.llm`` (see ``__main__``).
ALWAYS-ON: registered in ``_common/service_heartbeat.py``
``MONITORED_SERVICES`` — the stream watchdog starts/restarts it like
the other daemons; ``main()`` registers the heartbeat (the app-factory
form stays heartbeat-free).
"""
from __future__ import annotations

from typing import Optional

from .app import create_app
from .config import HostConfig
from .manager import ModelManager

__all__ = ["LocalModelHost"]


class LocalModelHost:
    """One resident host: config + manager + the wired FastAPI app."""

    def __init__(self, config: Optional[HostConfig] = None) -> None:
        self.config = config or HostConfig.load()
        self.manager = ModelManager(self.config)
        self.app = create_app(config=self.config, manager=self.manager)
