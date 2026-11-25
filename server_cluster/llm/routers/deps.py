"""server_cluster.llm.routers.deps — request-scoped
dependencies (the official FastAPI ``dependencies.py`` concept).

Routers never close over the manager/config from an app factory: they
resolve them per request through ``app.state`` (what ``create_app``
installed), so every router module stays importable and testable
independently. The ``find_*_backend`` helpers are the typed lookups
the ops/tokenization/ocr routers share (kind-filtered views over the
one registry). ``check_bearer`` is the host's ONE credential gate —
every model-consuming route (the four inference surfaces + the
management writes) runs it at handler top; the read-only liveness /
status GETs stay open."""
from __future__ import annotations

from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from ..backends.dummy import DummyChatBackend
from ..backends.ocr import OcrBackend
from ..backends.tokenization import TokenizationBackend
from ..config import HostConfig
from ..manager import ModelManager


def get_config(request: Request) -> HostConfig:
    """The host config ``create_app`` installed on app.state."""
    return request.app.state.config


def get_manager(request: Request) -> ModelManager:
    """The model manager ``create_app`` installed on app.state."""
    return request.app.state.manager


def check_bearer(request: Request, config: HostConfig,
                 manager: ModelManager) -> Optional[JSONResponse]:
    """The host's credential gate — the dummy-llm contract the chat
    route has enforced since the migration, now shared by every
    model-consuming route: a missing or malformed Bearer is 401 code
    1002; any non-empty value passes unless ``auth_token`` pins the
    config to exact-value matching. The dummy backend's auth counter
    books every rejection (the /stats surface). ``None`` = authorized,
    the handler proceeds."""
    auth = request.headers.get("authorization") or ""
    ok = auth.startswith("Bearer ") and len(auth) > 7
    if ok and config.auth_token:
        ok = auth[7:].strip() == config.auth_token
    if ok:
        return None
    dummy = find_dummy_backend(manager)
    if dummy is not None:
        dummy.note_auth_rejected()
    from .errors import openai_error

    return openai_error(
        401, "local model host: missing or malformed "
        "Authorization bearer header", code="1002")


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


def find_ocr_backend(manager: ModelManager) -> Optional[OcrBackend]:
    """The registry's OCR transcription model (kind ``ocr``) — None
    when not configured."""
    for model_id in manager.ids():
        backend = manager.get(model_id)
        if isinstance(backend, OcrBackend):
            return backend
    return None
