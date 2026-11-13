"""server_cluster._common.deps — request-scoped dependencies (the
official FastAPI ``dependencies.py`` concept, the llm-area host's
shape): routers never close over the collaborators from an app factory
— they resolve them per request through ``app.state`` (what
``create_app`` installed), so every router module stays importable and
testable independently."""
from __future__ import annotations

from fastapi import Request

from .config import AreaConfig
from .process_manager import ProcessManager
from .resident_supervisor import ResidentSupervisor
from .run_registry import RunRegistry


def get_config(request: Request) -> AreaConfig:
    """The area config ``create_app`` installed on app.state."""
    return request.app.state.config


def get_processes(request: Request) -> ProcessManager:
    """The child-process manager ``create_app`` installed on app.state."""
    return request.app.state.processes


def get_registry(request: Request) -> RunRegistry:
    """The run registry ``create_app`` installed on app.state."""
    return request.app.state.registry


def get_supervisor(request: Request) -> ResidentSupervisor:
    """The resident supervisor ``create_app`` installed on app.state."""
    return request.app.state.supervisor


def get_service_name(request: Request) -> str:
    """This server's heartbeat service name (== its module path)."""
    return request.app.state.config.service
