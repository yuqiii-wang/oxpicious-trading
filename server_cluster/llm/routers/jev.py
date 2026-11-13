"""server_cluster.llm.routers.jev — the System One router (the
standard Jev decision contract, thin over the jev backend).

``POST /v1/systemone`` — typed questions in, calibrated answers out:

    {"state": {"task": "..."},
     "questions": {"done": {"type": "noul", "instructions": "..."},
                   "next": {"type": "choice", "instructions": "...",
                            "criteria": {"click": "...", "stop": "..."}},
                   "progress": {"type": "score", "instructions": "...",
                                "criteria": ["not started", "done"]}}}
    → {"answers": {"done": {"noul": 0.93},
                   "next": {"probabilities": {"click": 0.71, ...}},
                   "progress": {"score": 1.0, ...}}}

Media is rejected (text-only serving — the OneJev weights are
multimodal; vision preprocessing is not wired). Our host serves several
jev models, so the optional ``model`` extension field routes (omitted
→ the single registered jev model; ambiguous → 400 with the ids). The
probability read runs via ``asyncio.to_thread`` (one forward under the
model's lock).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, Request

from ..backends.jev import JevBackend
from ..data_definitions.jev_api import SystemOneRequest
from ..manager import ModelManager
from .errors import model_load_failed, openai_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["jev"])


def _resolve_jev_backend(manager: ModelManager,
                         model: str | None) -> Any:
    """The named jev backend, the sole registered one, or None with a
    reason string — the router maps (None, reason) to the error."""
    ids = [mid for mid in manager.ids()
           if isinstance(manager.get(mid), JevBackend)]
    if model is not None:
        if model not in manager or not isinstance(manager.get(model),
                                                  JevBackend):
            return None, (f"`{model}` is not a registered jev model "
                          f"(registered: {ids or 'none'}).")
        return manager.get(model), None
    if not ids:
        return None, "no jev model is registered on this host."
    if len(ids) > 1:
        return None, (f"multiple jev models registered ({ids}) — pass "
                      "the `model` extension field.")
    return manager.get(ids[0]), None


@router.post("/v1/systemone")
async def systemone(request: Request, body: SystemOneRequest) -> Any:
    manager: ModelManager = request.app.state.manager
    backend, reason = _resolve_jev_backend(manager, body.model)
    if backend is None:
        return openai_error(404, reason, code="model_not_found")
    if body.media:
        return openai_error(
            400, "this host serves jev models TEXT-ONLY for now — the "
            "`media` field (images/video) is not supported yet.")
    manager.note_request(backend.model_id)
    try:
        answers, usage = await asyncio.to_thread(
            backend.systemone, body.state, body.media, body.questions)
    except Exception as exc:  # noqa: BLE001 — surfaced as 503
        manager.note_request(backend.model_id, error=True)
        logger.exception("systemone failed for %s", backend.model_id)
        return model_load_failed(backend.model_id, exc)
    # The response contract holds on OUR side too: the envelope and each
    # answer must satisfy the invariants + the request's criteria before
    # it leaves the host (a backend breach is an internal error, never a
    # silently malformed 200).
    try:
        return body.validate_response(
            backend.model_id, answers, usage=usage).model_dump()
    except ValueError as exc:  # includes pydantic ValidationError
        manager.note_request(backend.model_id, error=True)
        logger.exception("systemone contract breach for %s",
                         backend.model_id)
        return openai_error(
            500, f"model `{backend.model_id}` broke the systemone "
            f"response contract: {exc}",
            etype="internal_error", code="contract_violation")
