"""server_cluster.llm.data_definitions.chat_api — the OpenAI
chat-completions payload models.

The request side validates with pydantic (``model_validate`` on the raw
JSON body); the response side builds plain dicts with
``model_dump(exclude_none=True)`` so the JSON wire shape stays EXACTLY
the OpenAI one (null-free optional fields — the openai SDK tolerates
extra nulls but clean responses keep non-OpenAI clients honest).

Supported request fields: model, messages, temperature, top_p,
max_tokens (max_completion_tokens accepted as the modern alias), stop,
stream, stream_options.include_usage, n (only 1), user (echoed in the
response id... no — ignored). Everything else is tolerated-and-ignored
(the SDK sends some fields no local backend can honor, e.g. logprobs).

Text-only: a message content of content-parts is accepted when every
part is ``{"type": "text"}``; image parts raise
:class:`ImageContentNotSupported` (the route maps it to a 400 envelope)
— the OneJev backend serves TEXT chat; its vision preprocessing is not
exposed yet.
"""
from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import AliasChoices, BaseModel, Field, field_validator


class ImageContentNotSupported(ValueError):
    """A message carried image/audio content parts — the host is
    text-only for now."""


class ChatMessage(BaseModel):
    """One conversation message. ``content`` is a plain string; the
    parts-list form is flattened (text parts concatenated, image parts
    rejected) so backends only ever see str."""

    role: str = Field(description="system | user | assistant | tool")
    content: str = Field(default="")

    @field_validator("content", mode="before")
    @classmethod
    def _flatten_content(cls, value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            texts: list[str] = []
            for part in value:
                if not isinstance(part, dict):
                    raise ImageContentNotSupported(
                        f"unsupported content part {part!r}")
                ptype = part.get("type")
                if ptype == "text":
                    texts.append(str(part.get("text") or ""))
                elif ptype in ("image_url", "input_image", "image",
                               "file", "input_audio"):
                    raise ImageContentNotSupported(
                        f"content part type {ptype!r}: the local model "
                        "host serves text-only chat for now")
                else:
                    raise ImageContentNotSupported(
                        f"unsupported content part type {ptype!r}")
            return "".join(texts)
        return value


class StreamOptions(BaseModel):
    include_usage: bool = False


class ChatCompletionRequest(BaseModel):
    """The OpenAI ``POST /v1/chat/completions`` body (tolerant: unknown
    extra fields are ignored, not rejected — local backends honor the
    common core only)."""

    model: str = Field(description="The registered model_id to route to.")
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    max_tokens: Optional[int] = Field(
        default=None, ge=1,
        validation_alias=AliasChoices("max_tokens", "max_completion_tokens"),
        description="Completion budget (the modern "
        "max_completion_tokens alias binds to the same field).")
    stop: Optional[Union[str, list[str]]] = Field(
        default=None, description="Stop string(s); generation halts "
        "before the first hit and the hit is trimmed from the text.")
    stream: bool = False
    stream_options: Optional[StreamOptions] = None
    n: Optional[int] = Field(default=None, ge=1, le=1,
                             description="Only the OpenAI-default n=1.")
    request_id: Optional[str] = None  # ZhiPu-style passthrough

    model_config = {"populate_by_name": True}

    @field_validator("stop")
    @classmethod
    def _normalize_stop(cls, value: Optional[Union[str, list[str]]]
                        ) -> Optional[list[str]]:
        if value is None:
            return None
        stops = [value] if isinstance(value, str) else list(value)
        stops = [s for s in stops if s]
        return stops or None


def _now_epoch() -> int:
    import time

    return int(time.time())


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChoiceMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: Optional[str] = None


class ChatChoice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: Literal["stop", "length"] = "stop"
    logprobs: Optional[dict] = None


class ChatCompletionEnvelope(BaseModel):
    """The non-streaming response body (object == "chat.completion")."""

    id: str
    created: int = Field(default_factory=_now_epoch)
    model: str
    choices: list[ChatChoice]
    usage: Usage
    object: Literal["chat.completion"] = "chat.completion"


class DeltaMessage(BaseModel):
    role: Optional[Literal["assistant"]] = None
    content: Optional[str] = None


class ChatChunkChoice(BaseModel):
    index: int = 0
    delta: DeltaMessage
    finish_reason: Optional[Literal["stop", "length"]] = None
    logprobs: Optional[dict] = None


class ChatChunk(BaseModel):
    """One SSE ``chat.completion.chunk`` frame."""

    id: str
    created: int = Field(default_factory=_now_epoch)
    model: str
    choices: list[ChatChunkChoice] = Field(default_factory=list)
    usage: Optional[Usage] = None
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"


def error_body(message: str, *, etype: str = "invalid_request_error",
               code: Optional[str] = None) -> dict[str, Any]:
    """The OpenAI error envelope — ``{"error": {"message", "type",
    "code"?}}`` — what the openai SDK parses into its APIError types."""
    err: dict[str, Any] = {"message": message, "type": etype}
    if code is not None:
        err["code"] = code
    return {"error": err}


def new_completion_id() -> str:
    import uuid

    return f"chatcmpl-lmh-{uuid.uuid4().hex[:12]}"
