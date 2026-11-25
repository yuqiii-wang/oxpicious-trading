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

Media (images, the OCR contract's payload shape) is accepted when the
served jev backend is vision-capable (its weights carry an image
processor — ``vision_ready``): non-resident weights are loaded first
(a media request in the boot load window or after an idle unload must
not read as text-only), each request is decoded and magic-sniffed up
front (400 on any bad image) and the read conditions on the images. A
genuinely text-only backend (no image processor in the weights) keeps
the 400. Our host serves several jev models, so the optional ``model``
extension field routes (omitted → the single registered jev model;
ambiguous → 400 with the ids). The probability read runs via
``asyncio.to_thread`` (one forward under the model's lock).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, Request

from ..backends.jev import JevBackend
from ..data_definitions.jev_api import SystemOneRequest
from ..manager import ModelManager
from .deps import check_bearer
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


def _ensure_media_ready(backend: JevBackend) -> None:
    """Load the weights when they are not resident: ``vision_ready`` is
    only meaningful for a LOADED backend — a media request arriving in
    the boot load window or after an idle unload must cold-load (or
    wait out the in-progress load) BEFORE the capability check, or it
    would be answered with a bogus text-only 400 (the 2026-11-21
    backfill burned ~500 requests this way across the host's routine
    restarts). RuntimeError from a concurrent in-progress load maps to
    the 503 retry hint."""
    if backend.state == "loaded":
        return
    with backend.lock:
        backend.ensure_loaded()


@router.post("/v1/systemone")
async def systemone(request: Request, body: SystemOneRequest) -> Any:
    manager: ModelManager = request.app.state.manager
    config = request.app.state.config
    denied = check_bearer(request, config, manager)
    if denied is not None:
        return denied
    backend, reason = _resolve_jev_backend(manager, body.model)
    if backend is None:
        return openai_error(404, reason, code="model_not_found")
    media: list = []
    if body.media:
        try:
            await asyncio.to_thread(_ensure_media_ready, backend)
        except Exception as exc:  # noqa: BLE001 — a load failure is a 503
            manager.note_request(backend.model_id, error=True)
            logger.exception("media load failed for %s", backend.model_id)
            return model_load_failed(backend.model_id, exc)
        if not backend.vision_ready:
            return openai_error(
                400, f"`{backend.model_id}` serves jev reads TEXT-ONLY "
                "— its weights carry no image processor, so the "
                "`media` field cannot be conditioned on.")
        try:
            media = [image.decoded() for image in body.media]
        except ValueError as exc:
            return openai_error(
                400, f"invalid `media` image payload: {exc}")
    manager.note_request(backend.model_id)
    try:
        answers, usage = await asyncio.to_thread(
            backend.systemone, body.state, media, body.questions)
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
