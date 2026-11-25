"""builds.text.image_text — the news image → embedded-text enricher.

For each CONTENT image found in a news item/comment's src content (the
zhihu ``comment_img`` anchors — stickers, icons, avatars and ads are
pre-filtered before any download or model call; the 2026-11-21 artifact
census: image markup in content is ONLY ``<a>`` anchors — ``comment_img``
content shots, ``comment_sticker`` emoticons with ``data-sticker-id`` +
``data-width="0"``, ``member_mention`` links; zero ``<img>`` tags, and
avatars exist only at item level where the parser never carries them):

1. the resident local decision model (jev, ``POST /v1/systemone`` with
   ``media`` — vision reads wired 2026-11-21) answers the calibrated
   yes/no "is this a text screenshot" over the image itself,
2. on a confident yes the OCR model (PaddleOCR-VL, ``POST /v1/ocr``)
   transcribes it, and the anchor is REMOVED from the content (the URL
   is KEPT — it rides the fence's info string) while the transcription
   is appended as the fenced block

    ````ocr <url>
    <transcribed lines>
    ````

— a 4-backtick fence (survives 3-backtick runs inside a transcription)
whose info string carries the source URL. The image bytes are NEVER
stored: they live in memory for the duration of the read, keyed by
sha256 only as an in-run dedupe. The data_viz UI renders the block as
the distinct 图片文字 (OCR) panel — a different visual family from the
quote blockquote. Gate-no / failed-download / empty-transcription
images keep their anchor untouched (still viewable on the original
page).

Fail-soft by contract: any per-image failure (download, gate read, OCR)
leaves the content unmodified and counts into the stats — the build
never fails on this phase (the host being down warns once, like the jev
digestion side task).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import io
import json
import logging
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

# The fence: FOUR backticks — a transcription containing a 3-backtick
# run cannot close it early (the UI splitter matches the same shape).
FENCE_OPEN = "````ocr"
FENCE_CLOSE = "````"

# Keep the gate vocabulary IN SYNC with temp_scripts/self_test_jev.py —
# the battery's anchor probes exercise this exact request (the question
# texts are part of the serving contract; edits shift every verdict).
# THREE-way classification over TWO noul reads (the jev skill: k-way
# choice reads are position-biased — decompose into nouls, the residual
# is "other"): text screenshot / stats chart / other.
IMAGE_GATE_TASK = "判断附件图片是否为文字截图"
IMAGE_GATE_NOUL = ("该图片是文字截图（聊天记录、文章、文档等以文字为主的"
                   "内容），而非照片、图表或表情包")
IMAGE_GATE_QUESTION = "text_screenshot"
IMAGE_CHART_NOUL = ("该图片是统计图表（柱状图、折线图、饼图等数据图表），"
                    "而非文字截图、照片或表情包")
IMAGE_CHART_QUESTION = "stats_chart"

# The cloud comprehension read (charts): a data URL over this many
# base64 chars gets ONE JPEG re-encode, then the comprehension skips
# (the llm_ask payload cap's discipline — drop-oversized, 4M chars).
CLOUD_IMAGE_MAX_CHARS = 4 * 1024 * 1024

# The chart comprehension prompt (strict JSON — the model owns the
# schema; the fence-tolerant parser below absorbs prose leakage).
COMPREHEND_SYSTEM = (
    "你是数据图表解读助手。只输出一个JSON对象，不要输出任何其他文字。")
COMPREHEND_INSTRUCTION = (
    "解读这张数据图表截图，只输出一个JSON对象，结构："
    '{"kind": "line|bar|pie|table|mixed", "title": "图表标题", '
    '"summary": "图表要点总结，不超过60字", '
    '"points": [{"label": "数据点标签", "value": 数值, '
    '"series": "所属系列"}]}。'
    "points 只放关键数据点（5到12个，按图中顺序），value 用数字；"
    "多系列图用 series 区分；无法确定的值不要编造。")
COMPREHEND_MAX_TOKENS = 4096  # glm-5.3-flash's reasoning eats budget
# before the visible content (the ai_daily lesson: five empty replies
# at 200; dense 分时 charts truncated mid-JSON even at 2048 — the
# 2026-11-22 sweep's unparseable-reply diagnostics)

# zhimg hotlinks need a browser-ish UA and the zhihu referer; the bytes
# are capped at the local-model host's per-image payload cap (ocr_api).
DOWNLOAD_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
    "Referer": "https://www.zhihu.com/",
}
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024

# The content-image allowlist (the pre-filter's core): ONLY these
# anchors are candidates. comment_sticker / member_mention / avatar /
# ad markup never enters the lane by construction.
CONTENT_ANCHOR_RE = re.compile(
    r'<a\b[^>]*\bclass=["\']comment_img["\'][^>]*>[\s\S]*?</a>',
    re.IGNORECASE)
# Counted for the stats (visible proof the pre-filter works) — skipped.
STICKER_ANCHOR_RE = re.compile(r'\bclass=["\']comment_sticker["\']',
                               re.IGNORECASE)
# Future sources: generic image tags ride the same dimension guard.
IMG_TAG_RE = re.compile(r'<img\b[^>]*>', re.IGNORECASE)


def _attr(tag: str, name: str) -> Optional[str]:
    """One attribute value off a raw HTML tag (double or single
    quoted), or None."""
    match = re.search(rf'\b{name}=["\']([^"\']*)["\']', tag,
                      re.IGNORECASE)
    return match.group(1) if match else None


def _int_attr(tag: str, name: str) -> Optional[int]:
    raw = _attr(tag, name)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class ImageRef(BaseModel):
    """One content-image candidate parsed out of a content string."""

    url: str
    width: Optional[int] = None
    height: Optional[int] = None
    span: str = Field(description="The raw anchor/tag HTML — removed "
                       "from the content once parsed to text.")


class OcrReading(BaseModel):
    """One layout-mode OCR read: the clean transcription, the original
    image dims, and the spotted lines (``None`` when the model's read
    carried no spotting coordinates — the plain path)."""

    text: str
    width: int
    height: int
    truncated: bool = False
    elements: Optional[List[Dict[str, Any]]] = None


class ChartPoint(BaseModel):
    """One critical data point the comprehension read extracted."""

    label: str = Field(min_length=1, description="The point's label "
                       "(axis tick / slice / row header).")
    value: float = Field(description="The numeric value.")
    series: Optional[str] = Field(
        default=None, description="The series it belongs to "
        "(multi-series charts).")


class ChartComprehension(BaseModel):
    """The cloud vision read of a stats chart: a short summary plus the
    critical data points — enough for the UI's ROUGH reconstruction
    (exact pixels/series styling are deliberately not recovered)."""

    kind: str = Field(
        default="mixed", description="line | bar | pie | table | mixed.")
    title: Optional[str] = Field(default=None, description="The chart's "
                                 "title, when the image names one.")
    summary: str = Field(min_length=1, description="One or two "
                         "sentences — the chart's takeaway.")
    points: List[ChartPoint] = Field(
        default_factory=list, description="The critical data points, "
        "reading order (the reconstruction renders these only).")

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        allowed = {"line", "bar", "pie", "table", "mixed"}
        return value if value in allowed else "mixed"


def parse_chart_comprehension(text: str) -> Optional[ChartComprehension]:
    """The LLM answer → ChartComprehension, the news_digestions
    strict-JSON recipe: strip ``` fences, take the outermost {...}
    span, json.loads, validate. None on anything else (the caller
    keeps the anchor)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    try:
        return ChartComprehension.model_validate(payload)
    except ValidationError:
        return None


class ImageTextConfig(BaseModel):
    """Typed knobs of the image → text enrichment."""

    gate_threshold: float = Field(
        default=0.6, ge=0.0, le=1.0,
        description="The jev noul p_yes floor for 'text screenshot' — "
        "below it the image is left untouched.")
    chart_gate_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="The jev noul p_yes floor for 'stats chart' — the "
        "chart question's own calibration band tops out lower than the "
        "text one (2026-11-22 probes: obvious charts read 0.50–0.53, "
        "non-charts 0.05–0.08); dominance (chart > text) still required.")
    gate_timeout_s: float = Field(
        default=60.0, gt=0.0,
        description="Per-image systemone read timeout — a jev read is "
        "sub-second uncontended, but a concurrent OCR forward on the "
        "same GPU can starve it past 20s (the 2026-11-21 backfill's "
        "gate timeouts); 60s bounds the wait without a retry lane.")
    ocr_timeout_s: float = Field(
        default=300.0, gt=0.0,
        description="Per-image OCR transcription timeout — a dense "
        "screenshot (1260x2844 market table) can transcribe for "
        "minutes under the host's 8192-token ceiling; the 2026-11-21 "
        "backfill measured 25 timeouts at 120s, zero at 300s.")
    download_timeout_s: float = Field(default=20.0, gt=0.0)
    concurrency: int = Field(
        default=4, ge=1,
        description="Rows processed concurrently (the model hosts "
        "serialize behind their own locks anyway).")
    max_images_per_content: int = Field(
        default=4, ge=1,
        description="At most this many images embedded per content "
        "string (the rest keep their anchors).")
    max_ocr_chars: int = Field(
        default=1200, ge=100,
        description="Transcription tail cap per image (plain blocks).")
    max_layout_chars: int = Field(
        default=4800, ge=200,
        description="Layout block JSON-line budget per image — a "
        "coordinate line runs ~100 chars, so this covers ~40 lines; "
        "exceeding it falls back to the plain transcription block "
        "(1200 chars here made most real screenshots fall back — the "
        "2026-11-22 sweep's 26 plain reverts).")
    max_ocr_elements: int = Field(
        default=200, ge=1,
        description="Layout blocks: at most this many spotted lines "
        "(beyond it the block falls back to plain transcription).")
    comprehension_enabled: bool = Field(
        default=True,
        description="Chart-kind images get the cloud vision read (a "
        "glm-5.3-flash call per chart) — off leaves anchors untouched.")
    comprehension_model: str = Field(
        default="", description="Overrides the vision model id; EMPTY "
        "resolves through the secret registry (the llm row when its "
        "input_modality declares text+vision, else glm-5.3-flash).")
    comprehension_timeout_s: float = Field(
        default=120.0, gt=0.0,
        description="Per-chart cloud read timeout — the reasoning "
        "model's think time rides the same clock.")
    min_side_px: int = Field(
        default=120, ge=1,
        description="Dimension guard: a known width/height below this "
        "on any side is an icon/thumbnail/sticker — skipped.")


class NewsImageTextEnricher:
    """Content images → jev gate → OCR → the ````ocr fenced block.

    One instance per build run: the sha256 cache spans the run's rows
    (the same bytes gate/OCR once), the counters reset per enrich_rows
    call (asyncio increments them synchronously — no preemption between
    awaits, no lock needed).
    """

    def __init__(self, config: Optional[ImageTextConfig] = None):
        self.config = config or ImageTextConfig()
        self._seen_sha: Dict[str, Optional[str]] = {}
        self.counters: Dict[str, int] = defaultdict(int)

    # ------------------------------------------------------- extraction
    def extract_image_refs(self, content: str
                           ) -> Tuple[List[ImageRef], int]:
        """The content-image candidates + how many non-content anchors
        the pre-filter skipped (stickers etc.). Pure."""
        skipped = len(STICKER_ANCHOR_RE.findall(content))
        refs: List[ImageRef] = []
        for match in CONTENT_ANCHOR_RE.finditer(content):
            tag = match.group(0)
            url = (_attr(tag, "href") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                continue
            width = _int_attr(tag, "data-width")
            height = _int_attr(tag, "data-height")
            if self._too_small(width, height):
                skipped += 1
                continue
            refs.append(ImageRef(url=url, width=width, height=height,
                                 span=tag))
        for match in IMG_TAG_RE.finditer(content):
            tag = match.group(0)
            url = (_attr(tag, "src") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                continue
            width = _int_attr(tag, "width")
            height = _int_attr(tag, "height")
            if self._too_small(width, height):
                skipped += 1
                continue
            refs.append(ImageRef(url=url, width=width, height=height,
                                 span=tag))
        return refs, skipped

    def _too_small(self, width: Optional[int],
                   height: Optional[int]) -> bool:
        """The icon/thumbnail guard: declared dims below the floor on
        any side (absent dims pass — the jev gate backstops)."""
        dims = [d for d in (width, height) if d]
        return bool(dims) and min(dims) < self.config.min_side_px

    # ----------------------------------------------------------- reads
    def _download_sync(self, url: str) -> bytes:
        """The image bytes — browser-ish headers (zhimg hotlink
        protection), size-capped; never persisted."""
        response = requests.get(
            url, headers=DOWNLOAD_HEADERS,
            timeout=(5.0, self.config.download_timeout_s))
        response.raise_for_status()
        raw = response.content
        if not raw:
            raise ValueError("empty image payload")
        if len(raw) > MAX_DOWNLOAD_BYTES:
            raise ValueError(
                f"image payload of {len(raw)} bytes exceeds the "
                f"{MAX_DOWNLOAD_BYTES}-byte cap")
        return raw

    def _gate_sync(self, raw: bytes, ref: ImageRef) -> Dict[str, float]:
        """The jev vision read, THREE-way via TWO noul questions (the
        contract runs one forward per question, each conditioned on the
        image): ``{"text": P(text screenshot), "chart": P(stats
        chart)}`` — the residual is "other". Raises
        LocalModelHostError like any host failure."""
        from server_cluster.llm.client import LocalModelHostClient

        state: Dict[str, Any] = {
            "task": IMAGE_GATE_TASK,
            "image": "<image:0>",
            "width": ref.width,
            "height": ref.height,
        }
        response = LocalModelHostClient().systemone(
            state=state,
            questions={
                IMAGE_GATE_QUESTION: {
                    "type": "noul", "instructions": IMAGE_GATE_NOUL},
                IMAGE_CHART_QUESTION: {
                    "type": "noul", "instructions": IMAGE_CHART_NOUL},
            },
            media=[raw],
            read_timeout_s=self.config.gate_timeout_s)
        return {
            "text": float(
                response.answers[IMAGE_GATE_QUESTION].noul),
            "chart": float(
                response.answers[IMAGE_CHART_QUESTION].noul),
        }

    def _classify_gate(self, verdict: Dict[str, float]) -> str:
        """The two reads → ``text`` | ``chart`` | ``other``: each class
        needs ITS OWN floor (the chart question's calibration band tops
        out lower than the text one) plus dominance; a tie prefers text
        (charts with caption text must not hijack the text lane's
        calibrated verdict)."""
        p_text, p_chart = verdict["text"], verdict["chart"]
        if p_text >= self.config.gate_threshold and p_text >= p_chart:
            return "text"
        if (p_chart >= self.config.chart_gate_threshold
                and p_chart > p_text):
            return "chart"
        return "other"

    def _ocr_sync(self, raw: bytes) -> OcrReading:
        """The layout-mode OCR read (the model's text spotting —
        per-line boxes) + the original image dims for the block's
        coordinate space."""
        import io

        from PIL import Image
        from server_cluster.llm.client import LocalModelHostClient

        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
        response = LocalModelHostClient().ocr(
            images=[raw], layout=True,
            read_timeout_s=self.config.ocr_timeout_s)
        result = response.texts[0]
        elements = None
        if result.elements is not None:
            elements = [
                {"bbox": list(e.bbox), "text": e.text}
                for e in result.elements]
        return OcrReading(
            text=result.text, width=width, height=height,
            truncated=result.finish_reason == "length",
            elements=elements)

    # ------------------------------------------------- chart comprehension
    def _vision_model_candidates(
        self, config: Optional[Any],
    ) -> List[str]:
        """The comprehension model's candidate chain, first-verified
        first-used: an explicit knob override, then the registry's
        vision-declared llm row, then the known-good defaults. The
        declarations are RANKING hints — the live probe decides."""
        from llm_agents.llm_ask.core.vision import default_vision_model

        candidates: List[str] = []
        if self.config.comprehension_model:
            candidates.append(self.config.comprehension_model)
        if config is not None and config.model_name:
            candidates.append(config.model_name)
        candidates.append("glm-5.3-flash")
        fallback = default_vision_model("zhipu")
        if fallback:
            candidates.append(fallback)
        candidates.append("glm-4v-flash")  # the free fallback
        seen: set = set()
        return [m for m in candidates if not (m in seen or seen.add(m))]

    def _comprehend_sync(self, raw: bytes) -> Optional[ChartComprehension]:
        """The cloud vision read: VERIFY the candidate model's
        multimodality with one tiny image+text probe BEFORE the real
        chart lands in a prompt (declarations are hints — glm-4.5v /
        glm-4v are vision-declared and error 1113 no-balance on this
        account), then comprehend the stats chart into a short summary
        + critical data points as STRICT JSON. None on any failure —
        the caller keeps the anchor."""
        from llm_agents.deep_agents.llm_config import (
            LlmRuntimeConfig, build_chat_provider)
        from llm_agents.llm_ask.core.vision_probe import VisionProbe

        async def _ask() -> Optional[ChartComprehension]:
            config = await LlmRuntimeConfig.load()
            probe = VisionProbe(
                provider_label=config.provider if config else "zhipu",
                api_key=config.api_key if config else None,
                api_url=config.api_url if config else None)
            data_url = self._chart_data_url(raw)
            if data_url is None:
                return None
            messages: Optional[List[Dict[str, Any]]] = None
            model: Optional[str] = None
            for candidate in self._vision_model_candidates(config):
                if not await asyncio.to_thread(probe.verified, candidate):
                    continue
                model = candidate
                messages = [
                    {"role": "system", "content": COMPREHEND_SYSTEM},
                    {"role": "user", "content": [
                        {"type": "text", "text": COMPREHEND_INSTRUCTION},
                        {"type": "image_url",
                         "image_url": {"url": data_url}},
                    ]},
                ]
                break
            if messages is None or model is None:
                logger.warning(
                    "    [image-text] no vision-capable LLM verified "
                    "(candidates: %s) — comprehension skipped",
                    ", ".join(self._vision_model_candidates(config)))
                return None
            provider = build_chat_provider(
                config.provider if config else "zhipu",
                api_key=config.api_key if config else None,
                api_url=config.api_url if config else None)
            reply = await provider.chat_complete(
                messages, model=model, temperature=0.0,
                max_tokens=COMPREHEND_MAX_TOKENS)
            text = str(reply.get("content") or "")
            comprehension = parse_chart_comprehension(text)
            if comprehension is None:
                # the diagnostic the first sweep lacked: 36 silent
                # parse failures were truncated/empty reasoning-model
                # replies invisible to the counters
                logger.warning(
                    "    [image-text] comprehension unparseable — "
                    "reply head: %r", text[:200])
            return comprehension

        return asyncio.run(_ask())

    def _chart_data_url(self, raw: bytes) -> Optional[str]:
        """The image bytes → the multimodal part's data URL. Over the
        payload cap: ONE JPEG re-encode (charts survive q85 fine),
        then None — the drop-oversized discipline."""
        from server_cluster.llm.data_definitions.ocr_api import (
            sniff_image_mime)

        mime = sniff_image_mime(raw)
        encoded = base64.b64encode(raw).decode("ascii")
        if len(encoded) > CLOUD_IMAGE_MAX_CHARS:
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            mime = "jpeg"
        if len(encoded) > CLOUD_IMAGE_MAX_CHARS:
            logger.warning("    [image-text] chart image over the "
                           "payload cap even after re-encode — skipped")
            return None
        return f"data:image/{mime};base64,{encoded}"

    async def _chart_block(self, ref: ImageRef, raw: bytes
                           ) -> Optional[str]:
        """A chart-kind image → the fenced comprehension block (info
        string gains the ``chart`` kind token, body is the JSON); None
        (anchor kept) when comprehension is off/failed/too slow."""
        if not self.config.comprehension_enabled:
            return None
        try:
            comprehension = await asyncio.wait_for(
                asyncio.to_thread(self._comprehend_sync, raw),
                timeout=self.config.comprehension_timeout_s)
        except asyncio.TimeoutError:
            self.counters["comprehension_failed"] += 1
            logger.warning("    [image-text] comprehension timed out "
                           "(%ss) %s",
                           self.config.comprehension_timeout_s, ref.url)
            return None
        except Exception as exc:  # noqa: BLE001 — per-image fail-soft
            self.counters["comprehension_failed"] += 1
            logger.warning("    [image-text] comprehension failed %s: "
                           "%s: %s", ref.url, type(exc).__name__, exc)
            return None
        if comprehension is None:
            self.counters["comprehension_failed"] += 1
            return None
        self.counters["charts"] += 1
        body = comprehension.model_dump_json(exclude_none=True)
        return f"{FENCE_OPEN} {ref.url} chart\n{body}\n{FENCE_CLOSE}"

    async def _process_image(self, ref: ImageRef) -> Optional[str]:
        """One image → the fenced block, or None (gate no / any
        failure — the anchor stays). Raises nothing."""
        try:
            raw = await asyncio.to_thread(self._download_sync, ref.url)
        except Exception as exc:  # noqa: BLE001 — per-image fail-soft
            self.counters["failed"] += 1
            logger.warning("    [image-text] download failed %s: %s: %s",
                           ref.url, type(exc).__name__, exc)
            return None
        sha = hashlib.sha256(raw).hexdigest()
        if sha in self._seen_sha:
            return self._seen_sha[sha]
        block: Optional[str] = None
        try:
            self.counters["downloaded"] += 1
            verdict = await asyncio.to_thread(self._gate_sync, raw, ref)
            kind = self._classify_gate(verdict)
            if kind == "other":
                self.counters["gate_no"] += 1
                logger.info("    [image-text] gate other (text=%.3f "
                            "chart=%.3f) %s",
                            verdict["text"], verdict["chart"], ref.url)
                self._seen_sha[sha] = None
                return None
            if kind == "chart":
                # stats chart → the cloud comprehension read; a failed
                # comprehension keeps the anchor (no half-blocks).
                self.counters["gate_chart"] += 1
                block = await self._chart_block(ref, raw)
                if block is None:
                    self._seen_sha[sha] = None
                    return None
                self.counters["blocks"] += 1
                self._seen_sha[sha] = block
                return block
            self.counters["gate_yes"] += 1
            reading = await asyncio.to_thread(self._ocr_sync, raw)
            if not self._transcription_usable(reading.text):
                self.counters["ocr_empty"] += 1
                logger.info("    [image-text] unusable transcription %s",
                            ref.url)
                self._seen_sha[sha] = None
                return None
            self.counters["blocks"] += 1
            block = self._build_block(ref, reading)
        except Exception as exc:  # noqa: BLE001 — per-image fail-soft
            self.counters["failed"] += 1
            logger.warning("    [image-text] read failed %s: %s: %s",
                           ref.url, type(exc).__name__, exc)
            # NOT cached by sha — a transient host outage must not
            # poison the dedupe for later rows.
            return None
        self._seen_sha[sha] = block
        return block

    def _transcription_usable(self, text: str) -> bool:
        """The junk guard: a real transcription carries a varied
        vocabulary — a phone status bar OCRs to a handful of tokens
        repeated hundreds of times ('上午9:24 HD HD HD …'), which would
        pollute the content and its search_tsv. Empty covers trivial;
        the uniqueness floor covers the repetition case."""
        stripped = text.strip()
        if not stripped:
            return False
        tokens = stripped.split()
        if len(tokens) > 12 and len(set(tokens)) <= 5:
            return False
        return True

    def _build_block(self, ref: ImageRef, reading: OcrReading) -> str:
        """The fenced block. Layout variant when the spotting read
        produced usable elements: the info string carries the URL AND
        the original dims (``<url> <W>x<H>`` — the UI's coordinate
        space), the body is JSON-lines ``[x1,y1,x2,y2,"text"]`` in
        original-pixel reading order. Anything else (no elements, over
        the caps) falls back to the plain transcription block."""
        lines = self._layout_lines(reading)
        if lines is not None:
            body = "\n".join(lines)
            if reading.truncated:
                body += "\n……（转写截断）"
            return (f"{FENCE_OPEN} {ref.url} "
                    f"{reading.width}x{reading.height}\n{body}\n"
                    f"{FENCE_CLOSE}")
        text = html.unescape(reading.text.strip())
        truncated = reading.truncated
        if len(text) > self.config.max_ocr_chars:
            text = text[:self.config.max_ocr_chars].rstrip()
            truncated = True
        if truncated:
            text += "……（转写截断）"
        return f"{FENCE_OPEN} {ref.url}\n{text}\n{FENCE_CLOSE}"

    def _layout_lines(self, reading: OcrReading
                      ) -> Optional[List[str]]:
        """The block's JSON lines when the spotted elements fit the
        caps — ``None`` sends the block down the plain path."""
        if not reading.elements or not reading.width or not reading.height:
            return None
        if len(reading.elements) > self.config.max_ocr_elements:
            return None
        lines: List[str] = []
        total = 0
        for element in reading.elements:
            x1, y1, x2, y2 = element["bbox"]
            line = json.dumps([x1, y1, x2, y2, element["text"]],
                              ensure_ascii=False)
            total += len(line) + 1
            if total > self.config.max_layout_chars:
                return None
            lines.append(line)
        return lines

    # -------------------------------------------------------- enrichment
    async def enrich_content(self, content: str) -> str:
        """One content string → enriched (parsed anchors removed, the
        blocks appended) or unchanged. Fail-soft per image."""
        refs, skipped = self.extract_image_refs(content)
        self.counters["candidates"] += len(refs)
        self.counters["prefilter_skipped"] += skipped
        if not refs:
            return content
        blocks: List[str] = []
        parsed_spans: List[str] = []
        for ref in refs[:self.config.max_images_per_content]:
            block = await self._process_image(ref)
            if block:
                blocks.append(block)
                parsed_spans.append(ref.span)
        if not blocks:
            return content
        for span in parsed_spans:
            content = content.replace(span, "", 1)
        return content.rstrip() + "\n" + "\n".join(blocks)

    async def enrich_rows(self, rows: List[Dict[str, Any]],
                          *, key: str = "content") -> Dict[str, int]:
        """Enrich the given rows in place (rows whose content carries
        images only). Returns the phase stats — counters reset per
        call, the sha dedupe cache spans the run."""
        stats = {"rows": len(rows), "rows_scanned": 0,
                 "rows_changed": 0}
        self.counters = defaultdict(int)
        sem = asyncio.Semaphore(max(1, self.config.concurrency))

        async def run_one(row: Dict[str, Any]) -> None:
            content = row.get(key)
            if not content or not isinstance(content, str):
                return
            if "comment_img" not in content and "<img" not in content:
                return  # fast reject — no image markup at all
            stats["rows_scanned"] += 1
            async with sem:
                try:
                    enriched = await self.enrich_content(content)
                except Exception as exc:  # noqa: BLE001 — fail-soft
                    stats["rows_failed"] = stats.get("rows_failed", 0) + 1
                    logger.warning("    [image-text] row enrichment "
                                   "failed: %s: %s",
                                   type(exc).__name__, exc)
                    return
                if enriched != content:
                    row[key] = enriched
                    stats["rows_changed"] += 1

        await asyncio.gather(*(run_one(row) for row in rows))
        stats.update(self.counters)
        return dict(stats)
