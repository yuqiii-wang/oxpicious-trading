"""Chinese word segmentation via the local StructBERT token-classification model.

Wraps ``llm_agents/local_models/iic/nlp_structbert_word-segmentation_chinese-base``
(a BERT-style tagger emitting B/E/I/S-cws per character) as a process-wide
service for pipelines:

- Chinese-only: texts without CJK ideographs bypass the model entirely and
  come back unchanged (``[text]``); mixed text is segmented.
- bf16 on CUDA / fp32 on CPU — study-verified bit-identical output to fp32
  with half the VRAM (temp_scripts/probe_infer_frameworks.py).
- Post-processing forcing: re-joins digit/ASCII runs the model fragments
  (``报1830.5元`` -> ``报/1/8/30.5/元`` becomes ``报/1830.5/元``) and breaks
  wrong digit+CJK glues (``0.6亿`` -> ``80.6/亿``).  The aggressive tier
  additionally synthesizes number+unit words (``1830.5元``, ``2024年``,
  ``3.5万亿美元``).  Studies: temp_scripts/probe_postproc_number_fix.py,
  probe_regex_postproc_opt.py.
- Idle offload: a watchdog thread moves the model to RAM and empties the
  CUDA cache after ``idle_offload_s`` seconds (default 3600 — one hour)
  without calls; every segment call resets the countdown, so an
  interactive cadence of anything under an hour keeps the weights on GPU.
  The next call after an offload moves the model back to GPU
  transparently (weights stay resident in RAM).
- Auto-download: the model lives under the gitignored
  ``llm_agents/local_models/`` checkout; when its files are missing, first
  use downloads them from ModelScope (:func:`ensure_model_downloaded`).
- Logging: one summary line per large batch call (>= SUMMARY_LOG_MIN texts),
  DEBUG for smaller ones — never per text.  Lifecycle events (load, GPU
  offload/rehydrate) log at INFO.

The tag->words decode pipeline (``decode_words`` and its exception-forcing
helpers) lives on the class: it is pure (no torch) and works on a bare
instance — ``__init__`` only reads config.json for the label map; weights
load lazily on the first segment call.

Singleton via :meth:`ChineseWordSegmenter.get`; obtain one per process.
torch/transformers import lazily, so importing this package stays cheap for
services that never segment.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from transformers import PreTrainedModel, PreTrainedTokenizerFast

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_DIR = (
    REPO_ROOT / "llm_agents" / "local_models" / "iic"
    / "nlp_structbert_word-segmentation_chinese-base"
)

# CJK ideographs: extension A + unified + compatibility
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# --- exception-run patterns (possessive quantifiers => linear time) ---------
# core run: alnum, internal separators only between alnum chars, trailing %.
RE_RUN_CONS = re.compile(r"[0-9A-Za-z]++(?:[.,%]+[0-9A-Za-z]++)*+[％%]?+")
RE_RUN_AGGR = re.compile(r"[-+]?[0-9A-Za-z]++(?:[.,%:+]+[0-9A-Za-z]++)*+[％%]?+")
# unit chain attached after a number run (aggressive tier): 9,823亿元,
# 2024年, 3.5万亿美元; optional price compound (元/吨, 元/股, 元/手).
# 时/分/秒 excluded: "103分" vs "103分析" ambiguity — trim/extend as needed.
RE_UNIT = re.compile(
    r"(?:万亿|美元|港元|欧元|基点|亿|万|元|点|吨|年|月|日|手|股|倍|折)++"
    r"(?:/(?:吨|股|手|克))?+"
)
_UNIT_CHAIN_MAX = 3

MAX_TOKENS = 512  # model max_position_embeddings (incl. [CLS]/[SEP])
SUMMARY_LOG_MIN = 100  # batch sizes from here up log an INFO summary

# Canonical ModelScope repo for the weights (the .msc/.mv metadata in the
# model dir come from it; no HuggingFace mirror exists for this model).
MODELSCOPE_REPO_ID = "iic/nlp_structbert_word-segmentation_chinese-base"
_REQUIRED_MODEL_FILES = ("config.json", "tokenizer_config.json", "vocab.txt")
_WEIGHT_MODEL_FILES = ("pytorch_model.bin", "model.safetensors")


def _missing_model_files(model_dir: Path) -> list[str]:
    def absent(name: str) -> bool:
        p = model_dir / name
        return not p.is_file() or p.stat().st_size == 0

    missing = [f for f in _REQUIRED_MODEL_FILES if absent(f)]
    if all(absent(f) for f in _WEIGHT_MODEL_FILES):
        missing.extend(_WEIGHT_MODEL_FILES)
    return missing


def ensure_model_downloaded(model_dir: Path = DEFAULT_MODEL_DIR) -> None:
    """Download the model into ``model_dir`` from ModelScope when missing.

    No-op when the config/tokenizer/weight files are present and non-empty;
    otherwise fetches the full repo snapshot into ``model_dir``.  Raises
    RuntimeError when the modelscope download is impossible or does not
    yield the required files.  Not thread-safe by itself — the segmenter
    calls it from ``__init__`` under its lock; direct callers should hold
    their own sync.
    """
    missing = _missing_model_files(model_dir)
    if not missing:
        return
    logger.info(
        "word segmenter: model files missing %s in %s, downloading from "
        "modelscope %s", missing, model_dir, MODELSCOPE_REPO_ID,
    )
    model_dir.mkdir(parents=True, exist_ok=True)
    try:
        from modelscope import snapshot_download

        snapshot_download(MODELSCOPE_REPO_ID, local_dir=str(model_dir))
    except Exception as exc:
        raise RuntimeError(
            f"word segmenter model files {missing} missing in {model_dir} and "
            f"the modelscope download of {MODELSCOPE_REPO_ID} failed ({exc!r}). "
            "Install modelscope into the env, or download the model manually "
            f"into {model_dir}."
        ) from exc
    still_missing = _missing_model_files(model_dir)
    if still_missing:
        raise RuntimeError(
            f"word segmenter: modelscope snapshot finished but files still "
            f"missing in {model_dir}: {still_missing}"
        )
    logger.info("word segmenter: model downloaded into %s", model_dir)


class ChineseWordSegmenter:
    """Process-wide Chinese word segmenter (see module docstring).

    Public API: :meth:`segment` (one text -> words), :meth:`segment_batch`
    (texts -> word lists; non-Chinese passes through unchanged as
    ``[text]``), and :meth:`decode_words` (model tags -> words, pure and
    model-free).  Obtain via :meth:`get`; first parameters win.
    """

    _instance: ClassVar["ChineseWordSegmenter | None"] = None
    _instance_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def get(
        cls,
        model_dir: Path = DEFAULT_MODEL_DIR,
        device: str | None = None,
        aggressive: bool = False,
        idle_offload_s: float = 3600.0,
        batch_size: int = 16,
    ) -> "ChineseWordSegmenter":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(
                    model_dir=model_dir,
                    device=device,
                    aggressive=aggressive,
                    idle_offload_s=idle_offload_s,
                    batch_size=batch_size,
                )
            return cls._instance

    def __init__(
        self,
        model_dir: Path = DEFAULT_MODEL_DIR,
        device: str | None = None,
        aggressive: bool = False,
        idle_offload_s: float = 3600.0,
        batch_size: int = 16,
    ) -> None:
        self._model_dir = Path(model_dir)
        self._device = device or "cuda"
        self._aggressive = aggressive
        self._idle_offload_s = float(idle_offload_s)
        self._batch_size = int(batch_size)

        ensure_model_downloaded(self._model_dir)
        # label map straight from the model dir's config.json — decode_words
        # works on a bare instance without importing torch
        cfg = json.loads(
            (self._model_dir / "config.json").read_text(encoding="utf-8"))
        self._id2label: dict[int, str] = {
            int(k): v for k, v in cfg["id2label"].items()}
        # tag letter per id precomputed once — decode_words must not split
        # label strings per token on every call
        self._tag_of: dict[int, str] = {
            tid: lab.split("-", 1)[0] for tid, lab in self._id2label.items()}

        self._tokenizer: "PreTrainedTokenizerFast | None" = None
        self._model: "PreTrainedModel | None" = None
        self._loaded = False
        self._on_gpu = False
        self._resolved_device = "cpu"  # actual device after availability check
        self._last_use = 0.0
        self._lock = threading.Lock()
        self._watchdog: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ api
    def segment(self, text: str) -> list[str]:
        """One text -> word list.  Non-Chinese text returns ``[text]``."""
        return self.segment_batch([text])[0]

    def segment_batch(
        self, texts: list[str], batch_size: int | None = None
    ) -> list[list[str]]:
        """Batch API.  Chinese texts are segmented, non-Chinese texts pass
        through unchanged; order is preserved.  One summary log per call
        (INFO for large batches, DEBUG below SUMMARY_LOG_MIN)."""
        if not texts:
            return []
        self._touch()
        bs = batch_size or self._batch_size

        results: list[list[str]] = [[] for _ in texts]
        seg_idx = [i for i, t in enumerate(texts) if _CJK.search(t) is not None]
        seg_set = set(seg_idx)
        pass_idx = [i for i in range(len(texts)) if i not in seg_set]

        t0 = time.perf_counter()
        truncated = 0
        with self._lock:
            self._ensure_loaded()
            model_inputs: list[tuple[int, str]] = [(i, texts[i]) for i in seg_idx]
            for c in range(0, len(model_inputs), bs):
                chunk = model_inputs[c : c + bs]
                self._last_use = time.monotonic()  # keep-alive on long corpora
                words_per_text = self._forward_chunk([t for _, t in chunk])
                truncated += sum(1 for _, t in chunk if len(t) > MAX_TOKENS - 12)
                for (i, _), words in zip(chunk, words_per_text):
                    results[i] = words
        for i in pass_idx:
            results[i] = [texts[i]]

        self._touch()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        rate = len(seg_idx) / (elapsed_ms / 1000) if seg_idx and elapsed_ms > 0 else 0.0
        msg = (
            "segment batch summary: n=%d chars=%d segmented=%d passthrough=%d "
            "truncated~=%d batch_size=%d took=%.0fms (%.0f sents/s)"
        )
        args = (
            len(texts), sum(len(t) for t in texts), len(seg_idx), len(pass_idx),
            truncated, bs, elapsed_ms, rate,
        )
        if len(texts) >= SUMMARY_LOG_MIN:
            logger.info(msg, *args)
        else:
            logger.debug(msg, *args)
        return results

    def close(self) -> None:
        """Stop the idle-offload watchdog (model stays wherever it is)."""
        self._stop.set()

    # ------------------------------------------------- decode (pure, no torch)
    def decode_words(
        self,
        text: str,
        offsets: list[tuple[int, int]],
        tag_ids: list[int],
        *,
        id2label: dict[int, str] | None = None,
        aggressive: bool | None = None,
    ) -> list[str]:
        """Token tags -> word list: map tags through offsets to chars (a
        token's tag covers its whole span), apply exception forcing, then
        split on B/E/S boundaries.  Pure — works on a bare instance; the
        arguments default to this instance's label map and aggressiveness."""
        aggr = self._aggressive if aggressive is None else aggressive
        tag_of = (
            self._tag_of if id2label is None
            else {tid: lab.split("-", 1)[0] for tid, lab in id2label.items()}
        )

        char_tag: dict[int, str] = {}
        for (start, end), tid in zip(offsets, tag_ids):
            if end <= start:  # special / padding tokens have empty offsets
                continue
            tag = tag_of[tid]
            for pos in range(start, end):
                char_tag.setdefault(pos, tag)
        self._force_spans(text, char_tag, self._exception_spans(text, aggr))

        words: list[str] = []
        buf = ""
        get_tag = char_tag.get
        for i, ch in enumerate(text):
            tag = get_tag(i)
            if tag is None:  # uncovered char (e.g. ascii space) = separator
                if buf:
                    words.append(buf)
                    buf = ""
                continue
            if tag in ("B", "S") and buf:
                words.append(buf)
                buf = ""
            buf += ch
            if tag in ("E", "S"):
                words.append(buf)
                buf = ""
        if buf:
            words.append(buf)
        return words

    def _exception_spans(
        self, text: str, aggressive: bool
    ) -> list[tuple[int, int]]:
        """Char spans of non-CJK runs to force as single words.

        Aggressive mode widens the run pattern (leading sign, ``:``
        separator) and chains number+unit spans (``9,823亿元``, ``2024年``,
        ``3.5万亿美元``)."""
        pat = RE_RUN_AGGR if aggressive else RE_RUN_CONS
        spans: list[tuple[int, int]] = []
        for m in pat.finditer(text):
            s, e = m.span()
            if aggressive:
                for _ in range(_UNIT_CHAIN_MAX):
                    um = RE_UNIT.match(text, e)
                    if um is None:
                        break
                    e = um.end()
            spans.append((s, e))
        return spans

    @staticmethod
    def _force_spans(
        text: str, char_tag: dict[int, str], spans: list[tuple[int, int]]
    ) -> None:
        """Override char tags so each span decodes as one B..E (or S) word.

        Only positions the model actually tagged are touched, so truncated
        tails are never resurrected and text without runs stays untouched."""
        for s, e in spans:
            covered = [i for i in range(s, e) if i in char_tag]
            if not covered:
                continue
            if len(covered) == 1:
                char_tag[covered[0]] = "S"
            else:
                char_tag[covered[0]] = "B"
                for i in covered[1:-1]:
                    char_tag[i] = "I"
                char_tag[covered[-1]] = "E"

    # ------------------------------------------------------------- internals
    def _touch(self) -> None:
        self._last_use = time.monotonic()

    def _ensure_loaded(self) -> None:
        """Load weights on first use, or move them back to GPU after an idle
        offload.  Caller must hold ``self._lock``."""
        if self._loaded and (self._on_gpu or self._resolved_device == "cpu"):
            return
        import torch

        if not self._loaded:
            if self._device.startswith("cuda") and not torch.cuda.is_available():
                logger.warning("word segmenter: cuda unavailable, falling back to cpu")
                self._resolved_device = "cpu"
            else:
                self._resolved_device = self._device
            t0 = time.perf_counter()
            from transformers import (
                AutoModelForTokenClassification,
                AutoTokenizer,
            )

            self._tokenizer = AutoTokenizer.from_pretrained(self._model_dir)
            model = AutoModelForTokenClassification.from_pretrained(self._model_dir)
            # bf16 on CUDA: study-verified identical output, half the VRAM
            if self._resolved_device == "cuda":
                model = model.to(torch.bfloat16)
            self._model = model.to(self._resolved_device).eval()
            self._loaded = True
            self._on_gpu = self._resolved_device == "cuda"
            if self._watchdog is None:
                self._watchdog = threading.Thread(
                    target=self._idle_watchdog_loop, name="seg-idle-watchdog", daemon=True
                )
                self._watchdog.start()
            logger.info(
                "word segmenter: loaded %s on %s (%s) in %.1fs",
                self._model_dir.name, self._resolved_device,
                "bf16" if self._resolved_device == "cuda" else "fp32",
                time.perf_counter() - t0,
            )
        else:  # loaded but offloaded -> move back to GPU
            self._model.to("cuda")
            self._on_gpu = True
            logger.info("word segmenter: GPU rehydrate after idle offload")

    def _forward_chunk(self, chunk_texts: list[str]) -> list[list[str]]:
        """Tokenize + run the model + decode one chunk.  Lock held by caller."""
        import torch

        assert self._tokenizer is not None and self._model is not None
        enc = self._tokenizer(
            chunk_texts,
            is_split_into_words=False,  # neutralize tokenizer_config quirk
            padding=True,
            truncation=True,
            max_length=MAX_TOKENS,
            return_tensors="pt",
            return_offsets_mapping=True,
        )
        offsets = enc.pop("offset_mapping").tolist()
        enc = {k: v.to(self._resolved_device) for k, v in enc.items()}
        with torch.inference_mode():
            tag_ids = self._model(**enc).logits.argmax(-1).tolist()
        return [
            self.decode_words(t, off, ids)
            for t, off, ids in zip(chunk_texts, offsets, tag_ids)
        ]

    def _idle_watchdog_loop(self) -> None:
        check_s = max(1.0, min(30.0, self._idle_offload_s / 10.0))
        while not self._stop.wait(check_s):
            if not self._loaded or not self._on_gpu:
                continue
            idle = time.monotonic() - self._last_use
            if idle < self._idle_offload_s:
                continue
            with self._lock:
                # re-check under lock: a call may have started meanwhile
                idle = time.monotonic() - self._last_use
                if not self._on_gpu or idle < self._idle_offload_s:
                    continue
                import torch

                assert self._model is not None
                self._model.to("cpu")
                torch.cuda.empty_cache()
                self._on_gpu = False
                logger.info(
                    "word segmenter: GPU offload after %.0fs idle (weights kept in RAM)",
                    idle,
                )
