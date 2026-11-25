"""server_cluster.llm.backends.ocr — an OCR / vision-language
transcription model (kind ``ocr``) served through HF transformers.

PaddleOCR-VL (PaddlePaddle's 0.9B document-parsing VLM, ``model_type
paddleocr_vl`` — natively supported by the installed transformers as
``AutoModelForImageTextToText``) turns IMAGE FILES into their extracted
TEXT: the images go through ``AutoProcessor`` (NaViT dynamic
resolution, the chat template's ``<|IMAGE_START|><|IMAGE_PLACEHOLDER|>
<|IMAGE_END|>`` slot), one ``generate()`` per image under the model
lock, greedy by default (transcription is deterministic — the model
card serves it at temp 0).

The native contract is :meth:`transcribe` behind ``POST /v1/ocr``
(images as base64 / data URLs / multipart uploads → per-image text +
real tokenizer usage); the host's chat surface stays text-only (this
backend is not wired there — the jev backends own that route). The
model's published ``generation_config`` ships ``use_cache: false``;
serving passes ``use_cache=True`` so long transcriptions keep the KV
cache (a page of layout tokens without one is quadratic).

load/offload/unload machinery is the shared two-tier lifecycle (see
``.base``): bf16 on CUDA with a CUDA→CPU fallback at load, idle GPU→CPU
offload, RAM drop, lock-serialized inference. ``_warmup`` runs ONE tiny
transcription forward on a generated blank image with the lock ALREADY
held by ``ModelBackend.load`` — it calls the locked internals directly,
never :meth:`transcribe` (whose ``run_locked`` would re-acquire and
self-deadlock; the same trap the jev warm-up notes).
"""
from __future__ import annotations

import logging
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import ModelBackendSpec
from .base import DTYPE_MAP, ModelBackend

logger = logging.getLogger(__name__)

# The spotting decode's coordinate vocabulary: bare ``<|LOC_n|>`` value
# tokens (special=False in the tokenizer, so skip_special_tokens keeps
# them) in runs of EIGHT — the quad corners TL→TR→BR→BL, each axis
# normalized 0–1000 to the input image's extent.
OCR_LOC_TOKEN_RE = re.compile(r"<\|LOC_(\d+)\|>")
OCR_LOC_RUN_RE = re.compile(r"(?:<\|LOC_\d+\|>\s*){8}")


class OcrBackend(ModelBackend):
    """One OCR transcription model (kind ``ocr``) — see module
    docstring."""

    def __init__(self, spec: ModelBackendSpec,
                 default_idle_offload_s: float,
                 default_idle_unload_s: float) -> None:
        super().__init__(spec, default_idle_offload_s,
                         default_idle_unload_s)
        model_dir = spec.resolved_path()
        if model_dir is None or not Path(model_dir).is_dir():
            raise ValueError(
                f"{self.model_id}: the ocr kind requires an existing "
                f"model directory (got {spec.path!r})")
        self._model_dir = Path(model_dir)
        # Resolved at load time (device availability / dtype mapping).
        self._processor = None
        self._spotting_processor = None
        self._tokenizer = None
        self._model = None
        self._resolved_device = "cpu"
        self._resolved_dtype = spec.dtype

    # ------------------------------------------------------------ lifecycle
    def _load(self) -> None:
        import torch
        from transformers import (AutoModelForImageTextToText,
                                  AutoProcessor)

        device = self.spec.device
        if device.startswith("cuda") and not torch.cuda.is_available():
            logger.warning(
                "local model host: %s — cuda unavailable, falling back "
                "to cpu", self.model_id)
            device = "cpu"
        self._resolved_device = device
        dtype_name = DTYPE_MAP.get(self.spec.dtype.lower(),
                                   "torch.bfloat16")
        dtype = getattr(torch, dtype_name.split(".", 1)[1])

        t0 = time.perf_counter()
        self._processor = AutoProcessor.from_pretrained(self._model_dir)
        self._tokenizer = self._processor.tokenizer
        # The spotting recipe's processor (README: max_pixels lifted to
        # 2048*28*28 so thin screenshot lines survive); the default
        # processor keeps serving the plain OCR path. A kwarg rejection
        # (older transformers) falls back to the default processor —
        # spotting still works, the boxes just quantize coarser.
        try:
            self._spotting_processor = AutoProcessor.from_pretrained(
                self._model_dir, max_pixels=1605632)
        except Exception as exc:  # noqa: BLE001 — capability, not failure
            logger.info(
                "local model host: %s — per-call max_pixels rejected "
                "(%s); spotting reuses the default processor",
                self.model_id, exc)
            self._spotting_processor = self._processor
        model = AutoModelForImageTextToText.from_pretrained(
            self._model_dir)
        model = model.to(dtype).to(device).eval()
        self._model = model
        self._resolved_dtype = dtype_name
        logger.info(
            "local model host: %s weights on %s (%s) in %.1fs",
            self.model_id, device, dtype_name, time.perf_counter() - t0)

    def _offload(self) -> None:
        import torch

        assert self._model is not None
        self._model.to("cpu")
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _unload(self) -> None:
        """Drop the weights from RAM entirely — the next load() re-reads
        them from disk."""
        import gc

        self._model = None
        self._processor = None
        self._spotting_processor = None
        self._tokenizer = None
        gc.collect()

    def _warmup(self) -> None:
        """One minimal transcription forward — the served contract's
        exact path (templated image message + generate) on a generated
        blank image, run with the lock ALREADY held by
        ``ModelBackend.load`` (deliberately NOT :meth:`transcribe` —
        its ``run_locked`` re-acquires and self-deadlocks; the jev
        warm-up trap)."""
        from PIL import Image

        blank = Image.new("RGB", (64, 64), "white")
        self._transcribe_locked([blank], prompt="OCR:", temperature=0.0,
                                max_tokens=None)
        logger.info("local model host: %s warmed up (blank-image "
                    "transcription)", self.model_id)

    # --------------------------------------------------------------- inference
    def _sampling_kwargs(self, temperature: Optional[float],
                         ) -> Dict[str, Any]:
        """greedy on temperature 0/None (the OCR default — the model
        card serves it at temp 0); explicit sampling otherwise
        (transformers rejects sampling args alongside do_sample=False)."""
        kwargs: Dict[str, Any] = {}
        if temperature is not None and temperature > 0.0:
            kwargs.update(do_sample=True, temperature=float(temperature))
        else:
            kwargs.update(do_sample=False)
        return kwargs

    def _build_inputs(self, image: Any, prompt: str,
                      processor: Any = None) -> Dict[str, Any]:
        """One image + prompt → the model's device-resident input dict
        (the processor's chat template expands the image into the
        ``<|IMAGE_*|>`` token slots). ``processor`` overrides the
        default (the spotting path passes its max_pixels-lifted one).
        Lock held by caller."""
        processor = processor or self._processor
        assert processor is not None and self._model is not None
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": prompt},
        ]}]
        inputs = processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt")
        return {k: v.to(self._resolved_device) for k, v in inputs.items()}

    def transcribe(self, images: List[Any], *, prompt: str,
                   temperature: Optional[float] = 0.0,
                   max_tokens: Optional[int] = None,
                   layout: bool = False,
                   ) -> List[Tuple[str, str, Dict[str, int],
                                   Optional[List[Dict[str, Any]]]]]:
        """OCR contract: images in (PIL images or raw bytes of one of
        the supported raster formats), per-image transcription out —
        ``(text, finish_reason, usage, elements)`` per image, submission
        order preserved. The whole batch runs under ONE lock acquisition
        (each image is its own forward; a multi-image request keeps
        the GPU for its span). ``layout`` (the request's spotting mode)
        reads per-line boxes: ``elements`` carries
        ``{"bbox": (x1, y1, x2, y2), "text": str}`` dicts in ORIGINAL
        pixel space (reading order), ``None`` when the model's output
        carried no spotting coordinates — the ``text`` member stays the
        plain transcription either way."""
        return self.run_locked(lambda: self._transcribe_locked(
            images, prompt=prompt, temperature=temperature,
            max_tokens=max_tokens, layout=layout))

    def _transcribe_locked(self, images: List[Any], *, prompt: str,
                           temperature: Optional[float],
                           max_tokens: Optional[int],
                           layout: bool = False,
                           ) -> List[Tuple[str, str, Dict[str, int],
                                           Optional[List[Dict[str, Any]]]]]:
        import torch
        from PIL import Image

        assert self._processor is not None and self._model is not None
        budget = max_tokens or self.spec.max_new_tokens
        results: List[Tuple[str, str, Dict[str, int],
                            Optional[List[Dict[str, Any]]]]] = []
        for image in images:
            if isinstance(image, (bytes, bytearray)):
                image = Image.open(BytesIO(bytes(image)))
            orig_w, orig_h = image.size
            if layout:
                image = self._spotting_prepare(image)
            inputs = self._build_inputs(
                image, prompt,
                processor=self._spotting_processor if layout else None)
            input_len = int(inputs["input_ids"].shape[-1])
            kwargs: Dict[str, Any] = {
                "max_new_tokens": budget,
                "use_cache": True,  # the published generation_config
                # ships use_cache: false — serving keeps the KV cache.
            }
            kwargs.update(self._sampling_kwargs(temperature))
            with torch.inference_mode():
                out = self._model.generate(**inputs, **kwargs)
            new_ids = out[0][input_len:]
            text = self._tokenizer.decode(
                new_ids, skip_special_tokens=True)
            elements: Optional[List[Dict[str, Any]]] = None
            if layout:
                text, elements = self._parse_spotting(
                    text, orig_w, orig_h)
            finish = ("length" if int(new_ids.shape[-1]) >= budget
                      else "stop")
            usage = {
                "prompt_tokens": input_len,
                "completion_tokens": int(new_ids.shape[-1]),
                "total_tokens": input_len + int(new_ids.shape[-1]),
            }
            results.append((text, finish, usage, elements))
        self.touch()
        return results

    # ----------------------------------------------------- spotting parse
    @staticmethod
    def _spotting_prepare(image: Any) -> Any:
        """The README's spotting recipe: a small screenshot upscales 2x
        (LANCZOS) so thin lines survive the model's quantized read. The
        coordinate map back needs NO scale factor — the ``<|LOC_n|>``
        values are normalized to the input image's extent, and the
        upscale is aspect-preserving, so LOC/1000 × ORIGINAL dimension
        lands on the original pixel either way."""
        from PIL import Image

        width, height = image.size
        if width < 1500 and height < 1500:
            return image.resize((width * 2, height * 2),
                                Image.Resampling.LANCZOS)
        return image

    @staticmethod
    def _parse_spotting(text: str, orig_w: int, orig_h: int
                        ) -> Tuple[str, Optional[List[Dict[str, Any]]]]:
        """The spotting decode → (plain text, elements). The output is
        per-line text followed by EIGHT ``<|LOC_n|>`` tokens (the quad
        corners TL→TR→BR→BL, each axis 0–1000); the tokenizer's
        ``skip_special_tokens`` decode already stripped the
        ``<|LOC_BEGIN|>/<|LOC_SEP|>/<|LOC_END|>`` wrappers, leaving the
        bare value tokens. Quads reduce to axis-aligned boxes in
        original-pixel space. No location tokens at all → the read was
        not a spotting one: ``(text, None)`` — the caller's plain path."""
        runs = list(OCR_LOC_RUN_RE.finditer(text))
        if not runs:
            return text, None
        elements: List[Dict[str, Any]] = []
        cursor = 0
        for run in runs:
            line = text[cursor:run.start()].strip()
            cursor = run.end()
            if not line:
                continue
            values = [int(v) for v in OCR_LOC_TOKEN_RE.findall(run.group(0))]
            xs = [values[i] for i in (0, 2, 4, 6)]
            ys = [values[i] for i in (1, 3, 5, 7)]
            x1 = max(0, min(int(round(min(xs) / 1000 * orig_w)), orig_w))
            y1 = max(0, min(int(round(min(ys) / 1000 * orig_h)), orig_h))
            x2 = max(0, min(int(round(max(xs) / 1000 * orig_w)), orig_w))
            y2 = max(0, min(int(round(max(ys) / 1000 * orig_h)), orig_h))
            if x2 <= x1 or y2 <= y1:
                continue
            elements.append({"bbox": (x1, y1, x2, y2), "text": line})
        elements.sort(key=lambda e: (e["bbox"][1], e["bbox"][0]))
        # The layout path's plain member is the CLEAN reading-order
        # transcription — no <|LOC_n|> literals — so search / the
        # caller's plain fallback get usable text either way.
        clean = "\n".join(str(e["text"]) for e in elements)
        return clean, elements

    # --------------------------------------------------------------- status
    def _status_detail(self) -> dict:
        detail = super()._status_detail()
        detail.update({
            "path": str(self._model_dir),
            "resolved_device": self._resolved_device,
            "resolved_dtype": self._resolved_dtype,
            "loaded": self._model is not None,
        })
        return detail
