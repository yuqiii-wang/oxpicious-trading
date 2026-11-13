"""server_cluster._common.routers — the cluster API's router modules.

One APIRouter per resource slice (the llm-area host organization);
``ROUTER_MODULES`` is the app factory's only knowledge of the whole
surface."""
from __future__ import annotations

from . import health, jobs, processes

ROUTER_MODULES = (health, jobs, processes)
