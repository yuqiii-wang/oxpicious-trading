"""server_cluster.llm.routers.models_admin — the model
discovery + load/offload admin router (thin: HTTP only, the lifecycle
logic lives in the manager).

* ``GET /v1/models`` — the OpenAI list shape, each entry extended with
  the model's local lifecycle status under ``local``.
* ``GET /v1/models/{id}`` — OpenAI retrieve + ``local`` extension.
* ``POST /v1/models/{id}/load`` / ``POST /v1/models/{id}/offload`` —
  the admin load/offload API (idempotent, blocking until done; loads
  run via ``asyncio.to_thread`` so the event loop never blocks).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, Request

from ..data_definitions.admin import LoadOffloadResult
from ..manager import ModelManager
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


async def _load_offload(request: Request, model_id: str,
                        action: str) -> Any:
    manager: ModelManager = request.app.state.manager
    if model_id not in manager:
        return not_found_model(model_id)
    fn = manager.load if action == "load" else manager.offload
    try:
        status = await asyncio.to_thread(fn, model_id)
    except Exception as exc:  # noqa: BLE001 — load failures surface here
        logger.exception("admin %s failed for %s", action, model_id)
        return model_load_failed(model_id, exc)
    return LoadOffloadResult(model_id=model_id, action=action, status=status)


@router.post("/{model_id}/load")
async def load_model(request: Request, model_id: str) -> Any:
    return await _load_offload(request, model_id, "load")


@router.post("/{model_id}/offload")
async def offload_model(request: Request, model_id: str) -> Any:
    return await _load_offload(request, model_id, "offload")
