"""server_cluster.llm.routers.ocr — the OCR transcription router
(thin: HTTP/wire concerns only, over the ocr backend).

``POST /v1/ocr`` — IMAGE FILES in, the extracted TEXT out:

    {"images": [{"data": "data:image/png;base64,<payload>"}],
     "prompt": "OCR:",                      # optional, the default
     "model": "paddleocr-vl-1.6",           # optional routing extension
     "temperature": 0.0, "max_tokens": 8192}  # optional
    → 200 {"model": "paddleocr-vl-1.6", "prompt": "OCR:",
           "texts": [{"index": 0, "text": "...", "finish_reason": "stop",
                      "usage": {...}}],
           "usage": {...}}

Two wire forms carry the images — both decoded into the same
:class:`~..data_definitions.ocr_api.OcrRequest`:

* the JSON body above (base64 / data URLs),
* ``multipart/form-data`` uploads — each file part is one image, the
  optional non-file ``prompt`` field overrides the default prompt
  (the "take image files" form for plain HTTP clients).

The response contract is validated before it leaves the host
(:meth:`OcrRequest.validate_response` — one result per image, ordered
indexes, the summed usage), mirroring the systemone router. The
credential gate runs at handler top like every model-consuming route
(the shared ``check_bearer`` — the loopback bind is convenience, not
the access control). Inference runs via ``asyncio.to_thread`` (one
locked batch under the model lock).
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, List, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..backends.ocr import OcrBackend
from ..data_definitions.chat_api import Usage
from ..data_definitions.ocr_api import (OcrElement, OcrImageResult,
                                        OcrRequest, sniff_image_mime)
from ..manager import ModelManager
from .deps import check_bearer
from .errors import model_load_failed, openai_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ocr"])


def _resolve_ocr_backend(manager: ModelManager,
                         model: Optional[str]) -> Tuple[OcrBackend | None,
                                                        str | None]:
    """The named ocr backend, the sole registered one, or None with a
    reason string — the router maps (None, reason) to the error."""
    ids = [mid for mid in manager.ids()
           if isinstance(manager.get(mid), OcrBackend)]
    if model is not None:
        if model not in manager or not isinstance(manager.get(model),
                                                  OcrBackend):
            return None, (f"`{model}` is not a registered ocr model "
                          f"(registered: {ids or 'none'}).")
        return manager.get(model), None
    if not ids:
        return None, "no ocr model is registered on this host."
    if len(ids) > 1:
        return None, (f"multiple ocr models registered ({ids}) — pass "
                      "the `model` extension field.")
    return manager.get(ids[0]), None


async def _parse_request(request: Request
                         ) -> Tuple[Optional[OcrRequest], Optional[str]]:
    """JSON body → OcrRequest, or multipart file uploads → the same
    request (each file part one image, base64-wrapped for the shared
    decode path; the optional ``prompt`` field overrides the default).
    (None, reason) on any validation breach."""
    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        files: List[bytes] = []
        prompt: Optional[str] = None
        for key, value in form.multi_items():
            if hasattr(value, "read"):  # an UploadFile part
                files.append(await value.read())
            elif key == "prompt" and isinstance(value, str):
                prompt = value
        if not files:
            return None, "multipart request carried no file parts."
        for raw in files:
            try:
                sniff_image_mime(raw)
            except ValueError as exc:
                return None, str(exc)
        body = OcrRequest.model_validate({
            "images": [{"data": base64.b64encode(raw).decode("ascii")}
                       for raw in files],
            **({"prompt": prompt} if prompt else {}),
        })
        return body, None
    try:
        raw = await request.json()
    except Exception:  # noqa: BLE001 — malformed JSON
        return None, "invalid JSON request body"
    try:
        return OcrRequest.model_validate(raw), None
    except Exception as exc:  # noqa: BLE001 — pydantic ValidationError
        return None, f"invalid request body — {exc}"


@router.post("/v1/ocr")
async def ocr(request: Request) -> Any:
    manager: ModelManager = request.app.state.manager
    denied = check_bearer(request, request.app.state.config, manager)
    if denied is not None:
        return denied

    body, reason = await _parse_request(request)
    if body is None:
        return openai_error(400, reason)
    backend, reason = _resolve_ocr_backend(manager, body.model)
    if backend is None:
        return openai_error(404, reason, code="model_not_found")
    try:
        images = body.decoded_images()
    except ValueError as exc:
        return openai_error(400, str(exc))

    manager.note_request(backend.model_id)
    try:
        results = await asyncio.to_thread(
            backend.transcribe, [raw for raw, _mime in images],
            prompt=body.prompt, temperature=body.temperature,
            max_tokens=body.max_tokens, layout=body.layout)
    except Exception as exc:  # noqa: BLE001 — surfaced as 503
        manager.note_request(backend.model_id, error=True)
        logger.exception("ocr transcription failed for %s",
                         backend.model_id)
        return model_load_failed(backend.model_id, exc)
    texts = [
        OcrImageResult(index=i, text=text, finish_reason=finish,
                       usage=Usage(**usage),
                       elements=None if elements is None else [
                           OcrElement.model_validate(e) for e in elements])
        for i, (text, finish, usage, elements) in enumerate(results)]
    usage = Usage(
        prompt_tokens=sum(t.usage.prompt_tokens for t in texts),
        completion_tokens=sum(t.usage.completion_tokens for t in texts),
        total_tokens=sum(t.usage.total_tokens for t in texts),
    )
    try:
        response = body.validate_response(backend.model_id, texts, usage)
    except ValueError as exc:  # includes pydantic ValidationError
        manager.note_request(backend.model_id, error=True)
        logger.exception("ocr contract breach for %s", backend.model_id)
        return openai_error(
            500, f"model `{backend.model_id}` broke the ocr response "
            f"contract: {exc}",
            etype="internal_error", code="contract_violation")
    return JSONResponse(status_code=200, content=response.model_dump())
