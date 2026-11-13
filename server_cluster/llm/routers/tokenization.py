"""server_cluster.llm.routers.tokenization — the tokenization
LLM as request/response (thin router over the segmenter backend).

``POST /v1/tokenization/segment`` — ``{texts}`` in, ``{words}`` out
(the ``ChineseWordSegmenter.batch`` contract over HTTP): order
preserved, non-Chinese texts pass through server-side as ``[text]`` —
byte-identical to the in-process path. Blocking segmentation runs via
``asyncio.to_thread``; a failed load surfaces as an actionable 503.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request

from ..data_definitions.admin import SegmentRequest, SegmentResponse
from ..manager import ModelManager
from .deps import find_tokenization_backend
from .errors import model_load_failed, openai_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tokenization"])


@router.post("/v1/tokenization/segment", response_model=SegmentResponse)
async def tokenization_segment(request: Request, body: SegmentRequest
                               ) -> Any:
    manager: ModelManager = request.app.state.manager
    backend = find_tokenization_backend(manager)
    if backend is None:
        return openai_error(
            404, "no word-segmenter model is registered on this host.",
            code="model_not_found")
    manager.note_request(backend.model_id)
    try:
        words = await asyncio.to_thread(
            backend.segment_batch, list(body.texts), body.batch_size)
    except Exception as exc:  # noqa: BLE001 — surfaced as 503
        manager.note_request(backend.model_id, error=True)
        logger.exception("segmentation failed")
        return model_load_failed(backend.model_id, exc)
    total_words = sum(len(w) for w in words)
    return SegmentResponse(
        words=words, model=backend.model_id,
        usage={"prompt_tokens": total_words, "completion_tokens": 0,
               "total_tokens": total_words})
