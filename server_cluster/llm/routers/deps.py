"""server_cluster.llm.routers.deps — request-scoped
dependencies (the official FastAPI ``dependencies.py`` concept).

Routers never close over the manager/config from an app factory: they
resolve them per request through ``app.state`` (what ``create_app``
installed), so every router module stays importable and testable
independently. The two ``find_*_backend`` helpers are the typed lookups
the ops/tokenization routers share (kind-filtered views over the one
registry)."""
from __future__ import annotations

from typing import Optional

from fastapi import Request

from ..backends.dummy import DummyChatBackend
from ..backends.tokenization import TokenizationBackend
from ..config import HostConfig
from ..manager import ModelManager


def get_config(request: Request) -> HostConfig:
    """The host config ``create_app`` installed on app.state."""
    return request.app.state.config


def get_manager(request: Request) -> ModelManager:
    """The model manager ``create_app`` installed on app.state."""
    return request.app.state.manager


def find_dummy_backend(manager: ModelManager) -> Optional[DummyChatBackend]:
    """The registry's dummy simulation (the /control knobs' owner) —
    None when no dummy model is configured."""
    for model_id in manager.ids():
        backend = manager.get(model_id)
        if isinstance(backend, DummyChatBackend):
            return backend
    return None


def find_tokenization_backend(
        manager: ModelManager) -> Optional[TokenizationBackend]:
    """The registry's word segmenter (kind ``tokenization``) — None
    when not configured."""
    for model_id in manager.ids():
        backend = manager.get(model_id)
        if isinstance(backend, TokenizationBackend):
            return backend
    return None
