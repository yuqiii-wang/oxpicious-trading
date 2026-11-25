"""server_cluster.llm.routers.chat — the OpenAI-compatible
chat-completions router (thin: HTTP/wire concerns only).

``POST /v1/chat/completions`` and the ZhiPu mirror
``POST /api/paas/v4/chat/completions`` — one handler, the old
dummy-llm contract preserved verbatim where it applies:

* AUTH FIRST — a missing/malformed Bearer is 401 code 1002 before the
  body is even parsed (a real endpoint rejects an unauthenticated call
  regardless of body state); ``auth_token`` in the config tightens it
  to exact-value matching.
* Manual body validation (pydantic) → 400 in the OpenAI envelope.
* Routing on the ``model`` field: ``dummy-1`` → the migrated simulation
  envelope (429 bodies pass through with their zhipu codes; ``stream``
  is ignored, as the former server did); ``onejev-1.6-4b`` →
  transformers generation — full envelope non-streaming, SSE
  ``chat.completion.chunk`` frames + ``[DONE]`` when ``stream: true``.
* Non-chat models (the segmenter) → 400.

The SSE frame builder and the envelope assembly live HERE (they are
wire-format concerns); generation itself lives in the backend behind
the manager, serialized per model on the backend's lock.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from ..backends.jev import JevBackend, StreamDone
from ..backends.dummy import DummyChatBackend
from ..data_definitions.chat_api import (
    ChatChunk, ChatChunkChoice, ChatChoice, ChatCompletionEnvelope,
    ChatCompletionRequest, ChoiceMessage, DeltaMessage, Usage, error_body,
    new_completion_id,
)
from ..manager import ModelManager
from .deps import check_bearer
from .errors import model_load_failed, not_found_model, openai_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# The OpenAI-style path and the ZhiPu mirror (ZhipuChatProvider.CHAT_URL's
# path) — one handler serves both.
CHAT_PATHS = ("/v1/chat/completions", "/api/paas/v4/chat/completions")


# --------------------------------------------------- generation calls
async def _chat_once(manager: ModelManager, model_id: str,
                     backend: JevBackend,
                     body: ChatCompletionRequest) -> JSONResponse:
    """Non-streaming generation → the OpenAI envelope (blocking work via
    to_thread; the backend's lock serializes on one GPU)."""
    completion_id = new_completion_id()
    try:
        text, finish, usage = await asyncio.to_thread(
            backend.chat, body.messages,
            temperature=body.temperature, top_p=body.top_p,
            max_tokens=body.max_tokens, stop=body.stop)
    except Exception as exc:  # noqa: BLE001 — surfaced as 503
        manager.note_request(model_id, error=True)
        backend.note_error(exc)
        logger.exception("chat generation failed for %s", model_id)
        return model_load_failed(model_id, exc)
    envelope = ChatCompletionEnvelope(
        id=completion_id,
        model=model_id,
        choices=[ChatChoice(
            index=0,
            message=ChoiceMessage(content=text),
            finish_reason=finish)],
        usage=Usage(**usage),
    )
    return JSONResponse(status_code=200, content=envelope.model_dump())


async def _chat_stream_events(
    manager: ModelManager, model_id: str,
    backend: JevBackend, body: ChatCompletionRequest,
) -> AsyncIterator[str]:
    """SSE frames for one streaming completion (the backend's sync
    generator is pulled in a threadpool by Starlette; each frame is one
    ``data: {json}\\n\\n`` line, ``data: [DONE]\\n\\n`` last)."""
    completion_id = new_completion_id()
    created = int(time.time())
    include_usage = bool(
        body.stream_options and body.stream_options.include_usage)
    first_sent = False

    def frame(delta_role: Optional[str], delta_content: Optional[str],
              finish: Optional[str] = None, usage: Optional[Usage] = None,
              empty_choices: bool = False) -> str:
        chunk = ChatChunk(
            id=completion_id, created=created, model=model_id,
            choices=[] if empty_choices else [ChatChunkChoice(
                index=0,
                delta=DeltaMessage(role=delta_role, content=delta_content),
                finish_reason=finish)],
            usage=usage)
        return f"data: {json.dumps(chunk.model_dump(exclude_none=True))}\n\n"

    try:
        for event in backend.chat_stream(
                body.messages, temperature=body.temperature,
                top_p=body.top_p, max_tokens=body.max_tokens,
                stop=body.stop):
            if isinstance(event, StreamDone):
                if include_usage:
                    yield frame(None, None, usage=Usage(**event["usage"]),
                                empty_choices=True)
                yield frame(None, None, finish=event["finish_reason"])
                continue
            text = event["text"]
            if not first_sent:
                first_sent = True
                yield frame("assistant", text)
            else:
                yield frame(None, text)
        manager.note_request(model_id)
    except Exception as exc:  # noqa: BLE001 — mid-stream failure
        manager.note_request(model_id, error=True)
        backend.note_error(exc)
        logger.exception("streaming generation failed for %s", model_id)
        err = error_body(
            f"model `{model_id}` failed mid-stream: {exc!r}",
            etype="server_error", code="model_load_failed")
        yield f"data: {json.dumps(err)}\n\n"
    yield "data: [DONE]\n\n"


# ------------------------------------------------------------- handler
@router.post(CHAT_PATHS[0])
@router.post(CHAT_PATHS[1])
async def chat_completions(request: Request) -> Any:
    manager: ModelManager = request.app.state.manager
    config = request.app.state.config

    # AUTH FIRST (the old dummy-llm contract — a real endpoint rejects
    # an unauthenticated call regardless of any load or body state),
    # then manual body validation in the OpenAI error envelope.
    denied = check_bearer(request, config, manager)
    if denied is not None:
        return denied
    try:
        raw = await request.json()
    except Exception:  # noqa: BLE001 — malformed JSON
        return openai_error(400, "invalid JSON request body")
    try:
        body = ChatCompletionRequest.model_validate(raw)
    except ValidationError as exc:
        msg = "; ".join(
            "{}: {}".format(".".join(map(str, e.get("loc", []))),
                            e.get("msg"))
            for e in exc.errors())
        return openai_error(400, f"invalid request body — {msg}")

    model_id = body.model.strip()
    if model_id not in manager:
        manager.note_request(model_id, error=True)
        return not_found_model(body.model)
    backend = manager.get(model_id)

    if isinstance(backend, DummyChatBackend):
        # The migrated simulation: its envelope (or 429 body) verbatim;
        # stream is ignored, as the former server did.
        manager.note_request(model_id)
        try:
            out = await backend.chat_envelope(body.model_dump(exclude_none=True))
        except Exception as exc:  # noqa: BLE001
            manager.note_request(model_id, error=True)
            logger.exception("dummy-1 completion failed")
            return openai_error(500, f"dummy-1 completion failed: {exc!r}",
                                etype="server_error")
        if isinstance(out, dict) and "error" in out:
            manager.note_request(model_id, error=True)
            return JSONResponse(status_code=429, content=out)
        return JSONResponse(status_code=200, content=out)

    if not isinstance(backend, JevBackend):
        return openai_error(
            400, f"model `{model_id}` is not a chat completion model "
            f"(kind {type(backend).__name__}; jev models also serve "
            "POST /v1/systemone).")
    manager.note_request(model_id)
    if body.stream:
        return StreamingResponse(
            _chat_stream_events(manager, model_id, backend, body),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache",
                     "X-Accel-Buffering": "no"})
    return await _chat_once(manager, model_id, backend, body)
