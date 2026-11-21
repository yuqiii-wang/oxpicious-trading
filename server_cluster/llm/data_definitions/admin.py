"""server_cluster.llm.data_definitions.admin — the host's
management-endpoint payloads: per-model status (the load/offload API's
answer), the word-segmentation request/response, the migrated dummy
rate-limit controls, and the /health //stats summaries."""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class ModelStatus(BaseModel):
    """One model's lifecycle state — the admin API's answer body and the
    ``local`` extension on GET /v1/models/{id}. ``offloaded`` = weights
    in RAM off the GPU (tier 1), ``unloaded`` = no weights in memory,
    next use cold-loads (tier 2)."""

    model_id: str
    kind: str
    state: Literal["not_loaded", "loading", "loaded", "offloaded",
                   "unloaded", "error"] = "not_loaded"
    device: Optional[str] = None
    dtype: Optional[str] = None
    idle_for_s: Optional[float] = None
    last_use_epoch: Optional[float] = None
    error: Optional[str] = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ModelStatusList(BaseModel):
    models: list[ModelStatus]


class LoadOffloadResult(BaseModel):
    model_id: str
    action: Literal["load", "offload", "unload"]
    status: ModelStatus


class OffloadAllResult(BaseModel):
    """POST /v1/models/offload-all — the manual save-memory action's
    answer: only the models that were ``loaded`` and got offloaded now
    (already-offloaded/unloaded ones are skipped, a failing model is
    logged + skipped so the rest still offload)."""

    action: Literal["offload-all"] = "offload-all"
    offloaded: list[ModelStatus]


class SegmentRequest(BaseModel):
    """POST /v1/tokenization/segment — texts in, word lists out (the
    ChineseWordSegmenter.batch contract over HTTP). Non-Chinese texts
    pass through server-side as ``[text]``, exactly like in-process."""

    texts: list[str] = Field(min_length=1,
                             description="The texts to segment (order "
                             "preserved).")
    aggressive: bool = Field(default=False, description="Number+unit "
                             "word synthesis tier (1830.5元).")
    batch_size: Optional[int] = Field(default=None, ge=1, description=" "
                                      "Forward-chunk override.")


class SegmentResponse(BaseModel):
    words: list[list[str]] = Field(description="One word list per input "
                                   "text, position-aligned.")
    model: Optional[str] = Field(
        default=None,
        description="The serving segmenter model id (absent on older "
        "hosts).")
    usage: Optional[Dict[str, int]] = Field(
        default=None,
        description="Token consumption (host-supplied): the segmented "
        "word totals ARE the tokens — prompt_tokens / total_tokens = "
        "the word count, completion_tokens = 0.")


class ControlRequest(BaseModel):
    """One POST /control body — every field optional, absent = keep.
    Migrated verbatim from the former test.dummy_llm.server (the knobs
    affect ONLY the dummy-1 simulation)."""

    fail_next: Optional[int] = Field(default=None, ge=0,
                                     description="Make the next N dummy "
                                     "chat requests fail with the "
                                     "rate-limit error.")
    rate_limit_code: Optional[str] = Field(
        default=None, description="The zhipu-style error code the "
        "rate-limit failures carry (1302 | 1701 | 1113).")
    max_rpm: Optional[int] = Field(default=None, ge=0, description=" "
                                   "Sliding-window requests-per-minute "
                                   "cap; 0 clears the cap.")
    reset: bool = Field(default=False, description="Clear the window "
                        "and counters.")


class ControlResponse(BaseModel):
    """The controls' state as every /control (and GET /stats) answers —
    the exact shape the harness's HTTP-sim tests assert on."""

    fail_next: int
    rate_limit_code: str
    max_rpm: int
    served: int
    rejected_rate_limit: int
    rejected_auth: int
    # The cumulative token consumption the simulation has served (the
    # shared test.dummy_llm estimate formula) — the token-consumption
    # API beside the request counters; reset clears them.
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    uptime_s: float
    models: dict[str, str] = Field(
        description="model_id -> lifecycle state, one entry each.")


class StatsResponse(BaseModel):
    """GET /stats — host uptime + per-model request counters + the
    dummy controls' state (the HTTP-sim surface)."""

    service: str
    uptime_s: float
    per_model: dict[str, dict[str, Any]] = Field(
        description="model_id -> {requests, errors, ...}.")
    dummy_controls: ControlResponse
