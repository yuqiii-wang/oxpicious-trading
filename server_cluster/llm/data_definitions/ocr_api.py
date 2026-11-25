"""server_cluster.llm.data_definitions.ocr_api — the OCR API
payloads (the image-transcription contract, ``POST /v1/ocr``).

An OCR model (PaddleOCR-VL) turns IMAGE FILES into their extracted
TEXT — one transcription per image, images submitted as base64 / data
URLs in the JSON body or as multipart file uploads (the router decodes
both into the same request). The default ``prompt`` is the model
card's canonical text-recognition prompt (``"OCR:"``); the other
element-level prompts (``Formula Recognition:``, ``Table Recognition:``,
``Chart Recognition:``, ``Seal Recognition:``, ``Spotting:``) pass
through verbatim.

Like the systemone contract, the host may serve several OCR models —
the optional ``model`` extension field routes (omitted → the single
registered OCR model; ambiguous → error). The RESPONSE envelope is
``{"model", "prompt", "texts": [{index, text, finish_reason, usage}],
"usage"}`` and :meth:`OcrRequest.validate_response` is the full
contract check (one result per submitted image, ordered indexes,
summed usage) — the router validates its backend's output with it and
the HTTP client (``server_cluster.llm.client``) validates the wire
answer the same way.

Image validation (:meth:`OcrImageInput.decoded`,
:func:`sniff_image_mime`): the payload must decode as base64 and sniff
as one of the supported raster formats (PNG / JPEG / GIF / BMP / WebP
— what the model's image processor consumes) within the per-image size
cap; a data URL's declared mime is informational, the sniff decides.
"""
from __future__ import annotations

import base64
import binascii
from typing import List, Literal, Optional, Tuple

from pydantic import (AliasChoices, BaseModel, Field, field_validator,
                      model_validator)

from .chat_api import Usage

# The model card's canonical text-recognition prompt (the same default
# the PaddleOCR pipeline sends to a llama.cpp server of this model).
DEFAULT_OCR_PROMPT = "OCR:"

# The text-spotting prompt: per-line TEXT followed by EIGHT <|LOC_n|>
# tokens (the quadrilateral corners TL→TR→BR→BL, each axis normalized
# 0–1000) — the layout mode's fixed prompt (the model owns it).
SPOTTING_PROMPT = "Spotting:"

MAX_IMAGES_PER_REQUEST = 8

# Per-image decoded-size cap — a megapixel-scale page image is a few MB;
# 16 MB absorbs every resolution the image processor keeps anyway
# (its max_pixels default 1003520 ≈ 1 MP).
MAX_IMAGE_BYTES = 16 * 1024 * 1024

# Magic-byte signatures of the raster formats the processor consumes —
# (prefix, mime); the first matching prefix wins.
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"RIFF", "image/webp"),  # WebP: RIFF....WEBP — checked in sniff
)


def sniff_image_mime(data: bytes) -> str:
    """The image mime from magic bytes (WebP checked past its RIFF
    header) — ValueError when the bytes are not a supported raster
    format. Pure — shared by the JSON and multipart decode paths."""
    for prefix, mime in _IMAGE_MAGIC:
        if data.startswith(prefix):
            if mime == "image/webp" and data[8:12] != b"WEBP":
                continue
            return mime
    raise ValueError(
        "unsupported image payload — expected PNG / JPEG / GIF / BMP / "
        f"WebP bytes, got {data[:12]!r}")


def decode_b64_image(data: str) -> Tuple[bytes, str]:
    """One ``OcrImageInput.data`` string → (raw bytes, sniffed mime):
    a ``data:<mime>;base64,<payload>`` URL or a bare base64 payload,
    decoded, size-capped, magic-sniffed. ValueError on any breach."""
    payload = data.strip()
    if payload.lower().startswith("data:"):
        head, _, payload = payload.partition(",")
        if ";base64" not in head.lower():
            raise ValueError(
                "data URL must carry a base64 payload "
                f"(got {head!r})")
    try:
        raw = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"image payload is not valid base64: {exc}") from exc
    if not raw:
        raise ValueError("image payload decoded to zero bytes")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"image payload of {len(raw)} bytes exceeds the "
            f"{MAX_IMAGE_BYTES}-byte cap")
    return raw, sniff_image_mime(raw)


class OcrImageInput(BaseModel):
    """One image to transcribe: a data URL or bare base64 payload (the
    mime is sniffed from the decoded bytes — the declared one is
    informational only)."""

    data: str = Field(min_length=1, description="Data URL or bare "
                      "base64 of one image.")
    mime: Optional[str] = Field(
        default=None, description="Declared mime (informational — the "
        "sniff decides).")

    def decoded(self) -> Tuple[bytes, str]:
        return decode_b64_image(self.data)


class OcrRequest(BaseModel):
    """The ``POST /v1/ocr`` body (the standard fields + our optional
    ``model`` routing extension)."""

    images: List[OcrImageInput] = Field(
        min_length=1, max_length=MAX_IMAGES_PER_REQUEST,
        description=f"1..{MAX_IMAGES_PER_REQUEST} images; each is "
        "transcribed independently.")
    prompt: str = Field(
        default=DEFAULT_OCR_PROMPT, min_length=1,
        description="The element-level task prompt (``OCR:`` by "
        "default; ``Table Recognition:`` etc. pass through verbatim).")
    model: Optional[str] = Field(
        default=None, description="Route to a registered OCR model id "
        "(omitted → the single registered one).")
    temperature: Optional[float] = Field(
        default=0.0, ge=0.0, le=2.0,
        description="0 (default) = greedy — transcription is "
        "deterministic; >0 samples.")
    max_tokens: Optional[int] = Field(
        default=None, ge=1,
        validation_alias=AliasChoices("max_tokens", "max_completion_tokens"),
        description="Completion budget per image (the modern "
        "max_completion_tokens alias binds to the same field).")
    layout: bool = Field(
        default=False,
        description="TEXT-SPOTTING mode: per-line elements with their "
        "bounding boxes (the model's ``Spotting:`` read) come back in "
        "each result's ``elements`` — pixel coords in the ORIGINAL "
        "image space. The prompt is contract-owned in this mode.")

    model_config = {"populate_by_name": True}

    @field_validator("prompt")
    @classmethod
    def _prompt_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must be non-empty")
        return value

    @model_validator(mode="after")
    def _layout_owns_prompt(self) -> "OcrRequest":
        if self.layout:
            self.prompt = SPOTTING_PROMPT
        return self

    def decoded_images(self) -> List[Tuple[bytes, str]]:
        """All images decoded up front (the 400 boundary — a bad image
        fails the request before any model work)."""
        return [image.decoded() for image in self.images]

    def validate_response(self, model_id: str,
                          texts: List["OcrImageResult"],
                          usage: Usage) -> "OcrResponse":
        """The full response contract check: one result per submitted
        image, ordered indexes, per-result usage present, the summed
        usage matching. Raises ValueError (incl. pydantic
        ValidationError) on any breach."""
        if len(texts) != len(self.images):
            raise ValueError(
                f"expected {len(self.images)} texts (one per image), "
                f"got {len(texts)}")
        for i, result in enumerate(texts):
            if result.index != i:
                raise ValueError(
                    f"result index {result.index} out of order "
                    f"(expected {i})")
        summed = Usage(
            prompt_tokens=sum(r.usage.prompt_tokens for r in texts),
            completion_tokens=sum(
                r.usage.completion_tokens for r in texts),
            total_tokens=sum(r.usage.total_tokens for r in texts),
        )
        if (usage.prompt_tokens != summed.prompt_tokens
                or usage.completion_tokens != summed.completion_tokens
                or usage.total_tokens != summed.total_tokens):
            raise ValueError(
                f"response usage {usage.model_dump()} does not equal "
                f"the summed per-image usage {summed.model_dump()}")
        return OcrResponse(model=model_id, prompt=self.prompt,
                           texts=texts, usage=usage)


class OcrElement(BaseModel):
    """One spotted text line (layout mode): its axis-aligned bounding
    box in ORIGINAL-image pixel space (the spotting quads' four corners
    reduced to x1/y1/x2/y2) and the line's text."""

    bbox: Tuple[int, int, int, int] = Field(
        description="(x1, y1, x2, y2) — original-image pixels.")
    text: str = Field(min_length=1, description="The line's text.")


class OcrImageResult(BaseModel):
    """One image's transcription (mirrors the chat choice's accounting:
    finish_reason + the real tokenizer usage). ``elements`` carries the
    layout mode's spotted lines (absent on the plain OCR path)."""

    index: int = Field(ge=0, description="The submitted image's "
                       "0-based position.")
    text: str
    elements: Optional[List[OcrElement]] = Field(
        default=None, description="Layout mode: the spotted lines with "
        "their original-pixel bounding boxes, reading order.")
    finish_reason: Literal["stop", "length"] = "stop"
    usage: Usage


class OcrResponse(BaseModel):
    """The ``POST /v1/ocr`` response envelope."""

    model: str
    prompt: str
    texts: List[OcrImageResult]
    usage: Usage
