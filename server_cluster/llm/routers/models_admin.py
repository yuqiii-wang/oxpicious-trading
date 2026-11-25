"""server_cluster.llm.routers.models_admin — the model
discovery + load/offload admin router (thin: HTTP only, the lifecycle
logic lives in the manager).

* ``GET /v1/models`` — the OpenAI list shape, each entry extended with
  the model's local lifecycle status under ``local``.
* ``GET /v1/models/{id}`` — OpenAI retrieve + ``local`` extension.
* ``POST /v1/models/{id}/load`` / ``.../offload`` / ``.../unload`` —
  the admin lifecycle API (idempotent, blocking until done; loads run
  via ``asyncio.to_thread`` so the event loop never blocks). ``unload``
  drops the RAM weights of an ``offloaded`` model entirely (the tier-2
  action the idle watchdog itself drives).
* ``POST /v1/models/offload-all`` — the manual save-memory action: move
  every LOADED model's weights GPU → CPU right now (skipping
  already-offloaded ones; a failing model is skipped, not fatal). The
  operator's one-call VRAM free before the GPU-hungry pipelines.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, Request

from ..data_definitions.admin import LoadOffloadResult, OffloadAllResult
from ..manager import ModelManager
from .deps import check_bearer
from .errors import model_load_failed, not_found_model

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/models", tags=["models"])


@router.get("")
async def list_models(request: Request) -> Dict[str, Any]:
    manager: ModelManager = request.app.state.manager
    data = []
    for status in manager.status_all():
        data.append({
            "id": status.model_id,
            "object": "model",
            "created": 0,
            "owned_by": request.app.state.service_name,
            "local": status.model_dump(),
        })
    return {"object": "list", "data": data}


@router.get("/{model_id}")
async def retrieve_model(request: Request, model_id: str) -> Any:
    manager: ModelManager = request.app.state.manager
    if model_id not in manager:
        return not_found_model(model_id)
    status = manager.status_of(model_id)
    return {
        "id": status.model_id,
        "object": "model",
        "created": 0,
        "owned_by": request.app.state.service_name,
        "local": status.model_dump(),
    }


async def _lifecycle_action(request: Request, model_id: str,
                            action: str) -> Any:
    manager: ModelManager = request.app.state.manager
    denied = check_bearer(request, request.app.state.config, manager)
    if denied is not None:
        return denied
    if model_id not in manager:
        return not_found_model(model_id)
    fn = {"load": manager.load, "offload": manager.offload,
          "unload": manager.unload}[action]
    try:
        status = await asyncio.to_thread(fn, model_id)
    except Exception as exc:  # noqa: BLE001 — load failures surface here
        logger.exception("admin %s failed for %s", action, model_id)
        return model_load_failed(model_id, exc)
    return LoadOffloadResult(model_id=model_id, action=action, status=status)


@router.post("/{model_id}/load")
async def load_model(request: Request, model_id: str) -> Any:
    return await _lifecycle_action(request, model_id, "load")


@router.post("/{model_id}/offload")
async def offload_model(request: Request, model_id: str) -> Any:
    return await _lifecycle_action(request, model_id, "offload")


@router.post("/{model_id}/unload")
async def unload_model(request: Request, model_id: str) -> Any:
    return await _lifecycle_action(request, model_id, "unload")


@router.post("/offload-all")
async def offload_all_models(request: Request) -> OffloadAllResult:
    """The manual save-memory action — the collection-level companion
    to the per-model lifecycle routes (POST has no single-segment
    ``/{model_id}`` route, so the static path cannot shadow one)."""
    manager: ModelManager = request.app.state.manager
    denied = check_bearer(request, request.app.state.config, manager)
    if denied is not None:
        return denied
    offloaded = await asyncio.to_thread(manager.offload_all)
    return OffloadAllResult(offloaded=offloaded)
