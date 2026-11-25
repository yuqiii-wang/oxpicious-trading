"""server_cluster.llm.backends.jev — a Jev decision model
(kind ``jev``) served through HF transformers.

Jev models (TypeSafe's "System One" paradigm; OneJev is the OmniJev
open family fine-tuned from Qwen3.5) answer TYPED QUESTIONS about a
state with calibrated probabilities — the ``/v1/systemone`` API — the
counterpart of generative System-Two LLMs. This backend serves BOTH:

* the native System One contract (:meth:`systemone`) — one forward of
  the decision prompt, the last-position logits restricted to each
  answer's token set, softmax → calibrated probabilities:
  ``noul`` → P(yes) over the yes/no tokens; ``choice`` → one
  probability per option (A/B/C/...); ``score`` → the expected level
  over the ordered level tokens. Media (images) ride the SAME
  per-question forward when the weights carry an image processor
  (``AutoProcessor`` — the model is multimodal): the request's images
  become content parts at the ``<image:N>`` placeholder positions of
  the state text, and the probability read is unchanged on top of the
  vision-conditioned logits. A tokenizer-only weights dir keeps the
  text-only serving (``vision_ready`` False → the router 400s media).
* the generic OpenAI chat surface (:meth:`chat` / :meth:`chat_stream`)
  — a Jev model is still a Qwen text model, and the standard
  completion API stays useful for prose asks; the decision endpoint is
  the primary contract.

Prompt note: the fine-tune's exact decision slot format
(``onejev-slot-v2``) lives in the flide serving package; until it is
ported the prompt states the task, the typed questions and the lettered
options and demands a bare letter answer — the probability read (first
generated position over the answer tokens) follows the published
"calibrated probability in a single forward pass" behavior.

load/dtype/CUDA-fallback/streaming machinery (unchanged from this
file's ``chat_transformers.py`` predecessor): ``AutoTokenizer`` +
``AutoModelForImageTextToText`` first, ``AutoModelForCausalLM``
fallback; bf16 on CUDA (the repo's studied default) with a CUDA→CPU
fallback; ``apply_chat_template`` → ``model.generate`` under the model
lock (greedy at ``temperature == 0``, the model's generation_config
defaults when sampling is omitted); ``stop`` strings via
``StopStringCriteria``; ``TextIteratorStreamer`` in a worker thread
with the lock held for the whole stream. Requires transformers new
enough for the model's ``model_type`` (the OneJev weights need >= 5.x;
a too-old install surfaces as this backend's error state with an
actionable message, never taking the host down).
"""
from __future__ import annotations

import logging
import math
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from ..config import ModelBackendSpec
from .base import DTYPE_MAP, ModelBackend

logger = logging.getLogger(__name__)

# Worker-thread staleness bound for the streamer queue: a hung generate
# would otherwise block a reader forever; the bound only frees a wedged
# stream — real generation of this size always beats it comfortably.
_STREAM_TIMEOUT_S = 600.0

# The System One answer vocabularies: noul reads yes/no, choice/score
# read lettered options.
_NOUL_LABELS = ("yes", "no")
_CHOICE_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class StreamDone(Dict[str, Any]):
    """The terminal stream event: finish_reason + usage."""


def _softmax(values: Sequence[float]) -> List[float]:
    """Numerically stable softmax — the probability read's core."""
    top = max(values)
    exps = [math.exp(v - top) for v in values]
    total = sum(exps)
    return [e / total for e in exps]


def _label_token_candidates(tokenizer: Any, label: str) -> List[int]:
    """The candidate FIRST token ids of one answer label (with and
    without a leading space — tokenizers differ on word-initial
    encoding; the max logit across candidates wins). Candidates whose
    first token is whitespace-only are dropped unless they are all
    there is: a leading-space encoding of a CJK label starts with a
    bare space token, and that SHARED candidate would correlate every
    label's read through one logit (the 2026-11-11 self-test: all CJK
    options forced to a uniform distribution)."""
    ids: List[int] = []
    for text in (label, f" {label}"):
        try:
            enc = tokenizer.encode(text, add_special_tokens=False)
        except Exception:  # noqa: BLE001 — exotic vocabs
            enc = []
        if enc:
            ids.append(enc[0])
    meaningful = [tid for tid in set(ids)
                  if tokenizer.decode([tid]).strip()]
    return sorted(meaningful or set(ids))


def _label_scores(logits_row: Sequence[float],
                  candidates_per_label: Sequence[Sequence[int]]) -> List[float]:
    """Per-label score = the max logit across the label's candidate
    first tokens (pure — unit-probed without a model)."""
    scores: List[float] = []
    for candidates in candidates_per_label:
        scores.append(max(logits_row[tid] for tid in candidates))
    return scores


class JevBackend(ModelBackend):
    """One Jev decision model (kind ``jev``) — see module docstring."""

    def __init__(self, spec: ModelBackendSpec,
                 default_idle_offload_s: float,
                 default_idle_unload_s: float) -> None:
        super().__init__(spec, default_idle_offload_s,
                         default_idle_unload_s)
        model_dir = spec.resolved_path()
        if model_dir is None or not Path(model_dir).is_dir():
            raise ValueError(
                f"{self.model_id}: the jev kind requires an existing "
                f"model directory (got {spec.path!r})")
        self._model_dir = Path(model_dir)
        # Resolved at load time (device availability / dtype mapping).
        self._tokenizer = None
        self._processor = None  # image processor — vision reads only
        self._model = None
        self._resolved_device = "cpu"
        self._resolved_dtype = spec.dtype

    # ------------------------------------------------------------ lifecycle
    def _load(self) -> None:
        import torch
        from transformers import AutoTokenizer

        device = self.spec.device
        if device.startswith("cuda") and not torch.cuda.is_available():
            logger.warning(
                "local model host: %s — cuda unavailable, falling back "
                "to cpu", self.model_id)
            device = "cpu"
        self._resolved_device = device
        dtype_name = DTYPE_MAP.get(self.spec.dtype.lower(), "torch.bfloat16")
        dtype = getattr(torch, dtype_name.split(".", 1)[1])

        t0 = time.perf_counter()
        self._tokenizer = AutoTokenizer.from_pretrained(self._model_dir)
        self._processor = self._load_image_processor()
        model = self._load_model_class()
        model = model.to(dtype).to(device).eval()
        self._model = model
        self._resolved_dtype = dtype_name
        logger.info(
            "local model host: %s weights on %s (%s) in %.1fs",
            self.model_id, device, dtype_name, time.perf_counter() - t0)

    def _load_model_class(self):
        """``AutoModelForImageTextToText`` first (conditional-generation
        multimodal architectures), then ``AutoModelForCausalLM`` — with
        an actionable error when the installed transformers knows neither
        (the config's ``model_type`` is named in the message)."""
        import json

        from transformers import AutoModelForCausalLM

        try:
            from transformers import AutoModelForImageTextToText

            return AutoModelForImageTextToText.from_pretrained(
                self._model_dir)
        except Exception as exc:  # noqa: BLE001 — try the causal-lm mapping
            logger.info(
                "local model host: %s — AutoModelForImageTextToText "
                "unusable (%s); trying AutoModelForCausalLM",
                self.model_id, exc)
        try:
            return AutoModelForCausalLM.from_pretrained(self._model_dir)
        except Exception as exc:  # noqa: BLE001
            model_type = "unknown"
            try:
                cfg = json.loads(
                    (self._model_dir / "config.json").read_text(
                        encoding="utf-8"))
                model_type = str(cfg.get("model_type", "unknown"))
            except Exception:  # noqa: BLE001 — the original error matters more
                pass
            raise RuntimeError(
                f"{self.model_id}: the installed transformers cannot load "
                f"model_type={model_type!r} from {self._model_dir} ({exc!r})"
                " — upgrade transformers or disable this model in "
                "host_config.json") from exc

    def _load_image_processor(self):
        """The weights' image processor when present — ``None`` (and a
        logged note) on a tokenizer-only weights dir, which keeps the
        serving text-only: media requests are rejected by the router
        (``vision_ready`` False) instead of failing at forward time.
        A processor_config.json that FAILS to load is loud (ERROR) —
        the weights are expected to be vision-capable, and the silent
        downgrade otherwise hides real breakage (the 2026-11-21 host
        restart served ~300 text-only 400s before anyone noticed)."""
        try:
            from transformers import AutoProcessor

            processor = AutoProcessor.from_pretrained(self._model_dir)
        except Exception as exc:  # noqa: BLE001 — absence is a mode, not a failure
            if (self._model_dir / "processor_config.json").is_file():
                logger.error(
                    "local model host: %s — processor_config.json "
                    "PRESENT but AutoProcessor failed (%s); media "
                    "reads are rejected as text-only until a reload — "
                    "reload the model",
                    self.model_id, exc)
            else:
                logger.info(
                    "local model host: %s — no image processor in the "
                    "weights (%s); media reads are rejected as "
                    "text-only", self.model_id, exc)
            return None
        if not hasattr(processor, "image_processor"):
            logger.info(
                "local model host: %s — the loaded processor carries "
                "no image processor; media reads are rejected as "
                "text-only", self.model_id)
            return None
        return processor

    @property
    def vision_ready(self) -> bool:
        """True when media (image) reads can be served — the weights
        loaded an image processor. The router checks this BEFORE
        dispatch and 400s media against a text-only backend."""
        return self._processor is not None

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
        self._tokenizer = None
        self._processor = None
        gc.collect()

    def _warmup(self) -> None:
        """One minimal System One forward — the served contract's exact
        path (templated prompt + last-position logits), run with the
        lock ALREADY held by ``ModelBackend.load``. Deliberately NOT
        ``chat_stream``: it acquires the lock, and ``_warmup`` runs
        inside load()'s locked section — the re-acquire self-deadlocked
        every warm-up (latent while the weights could not load at all;
        exposed by the transformers 5.x upgrade)."""
        self._logits_after_prompt(
            [{"role": "user", "content": "ping"}])[0]
        logger.info("local model host: %s warmed up (systemone forward)",
                    self.model_id)

    # ------------------------------------------------------------ generation
    def _messages_dicts(self, messages) -> List[Dict[str, str]]:
        """The chat route's typed messages AND the systemone path's
        plain dicts (``_decision_prompt`` builds dicts — the wire shape
        apply_chat_template takes) both land here."""
        return [
            m if isinstance(m, dict) else
            {"role": m.role, "content": m.content}
            for m in messages
        ]

    def _sampling_kwargs(
        self, temperature: Optional[float], top_p: Optional[float],
    ) -> Dict[str, Any]:
        """greedy on temperature==0; the model's generation_config
        defaults when the request omits sampling; explicit sampling
        otherwise (transformers rejects sampling args alongside
        do_sample=False)."""
        import torch

        kwargs: Dict[str, Any] = {}
        if temperature is not None and temperature > 0.0:
            kwargs.update(
                do_sample=True, temperature=float(temperature),
                top_p=float(top_p) if top_p is not None else 1.0)
        elif temperature == 0.0:
            kwargs.update(do_sample=False)
        if kwargs.get("do_sample"):
            kwargs["pad_token_id"] = (
                self._tokenizer.pad_token_id
                or self._tokenizer.eos_token_id)
        return kwargs

    def _stop_criteria(self, stop: Optional[List[str]]):
        if not stop:
            return None
        from transformers import StopStringCriteria

        return StopStringCriteria(tokenizer=self._tokenizer,
                                  stop_strings=list(stop))

    @staticmethod
    def _trim_at_stop(text: str, stop: Optional[List[str]]) -> str:
        if not stop:
            return text
        cut = min((text.find(s) for s in stop if text.find(s) >= 0),
                  default=-1)
        return text[:cut] if cut >= 0 else text

    def _prepare_inputs(self, messages, images: Optional[List[Any]] = None):
        """The templated prompt → the device-resident input dict. Text
        reads template via the tokenizer as before; image reads fill
        the ``{"type": "image"}`` content parts with the PIL images (in
        the part order the decision prompt returned) and template
        through the processor — the same call shape the OCR backend
        proves out (the processor expands the parts into the model's
        image-token slots and adds ``pixel_values``)."""
        import torch

        assert self._tokenizer is not None and self._model is not None
        if images and self._processor is not None:
            pending = list(images)
            filled: List[Dict[str, Any]] = []
            for message in self._messages_dicts(messages):
                content = message.get("content")
                if not isinstance(content, list):
                    filled.append(message)
                    continue
                parts: List[Dict[str, Any]] = []
                for part in content:
                    if isinstance(part, dict) and \
                            part.get("type") == "image" and pending:
                        parts.append({"type": "image",
                                      "image": pending.pop(0)})
                    else:
                        parts.append(part)
                filled.append({**message, "content": parts})
            if pending:
                raise ValueError(
                    f"{len(pending)} image(s) without an ``<image:N>`` "
                    "slot in the decision prompt")
            prompt = self._processor.apply_chat_template(
                filled, tokenize=True, add_generation_prompt=True,
                return_dict=True, return_tensors="pt")
        else:
            prompt = self._tokenizer.apply_chat_template(
                self._messages_dicts(messages),
                tokenize=True, add_generation_prompt=True, return_dict=True,
                return_tensors="pt")
        prompt = {k: v.to(self._resolved_device)
                  for k, v in prompt.items()}
        input_len = int(prompt["input_ids"].shape[-1])
        return prompt, input_len

    def _finish_and_usage(self, new_token_count: int, max_tokens: int,
                          text: str, input_len: int) -> Tuple[str, Dict]:
        finish = "length" if new_token_count >= max_tokens else "stop"
        usage = {
            "prompt_tokens": input_len,
            "completion_tokens": new_token_count,
            "total_tokens": input_len + new_token_count,
        }
        return finish, usage

    def chat(self, messages, *, temperature: Optional[float] = None,
             top_p: Optional[float] = None, max_tokens: Optional[int] = None,
             stop: Optional[List[str]] = None
             ) -> Tuple[str, str, Dict[str, int]]:
        """One full generation -> (text, finish_reason, usage dict)."""
        budget = max_tokens or self.spec.max_new_tokens

        def run() -> Tuple[str, str, Dict[str, int]]:
            import torch

            prompt, input_len = self._prepare_inputs(messages)
            kwargs: Dict[str, Any] = {"max_new_tokens": budget}
            kwargs.update(self._sampling_kwargs(temperature, top_p))
            criteria = self._stop_criteria(stop)
            if criteria is not None:
                kwargs["stopping_criteria"] = [criteria]
            with torch.inference_mode():
                out = self._model.generate(**prompt, **kwargs)
            new_ids = out[0][input_len:]
            text = self._tokenizer.decode(
                new_ids, skip_special_tokens=True)
            text = self._trim_at_stop(text, stop)
            finish, usage = self._finish_and_usage(
                int(new_ids.shape[-1]), budget, text, input_len)
            return text, finish, usage

        return self.run_locked(run)

    def chat_stream(self, messages, *, temperature: Optional[float] = None,
                    top_p: Optional[float] = None,
                    max_tokens: Optional[int] = None,
                    stop: Optional[List[str]] = None
                    ) -> Iterator[Dict[str, Any]]:
        """Generation as an event stream: ``{"type": "delta", "text"}``
        frames, then one ``StreamDone(finish_reason=..., usage=...)``.
        The model lock is held from the first frame to exhaustion — the
        route must consume the iterator fully (it does; client
        disconnects close the generator, releasing the lock). The
        generation runs in a worker thread; the streamed text is the
        tokenizer's incremental decode (a `stop` string mid-stream is
        NOT trimmed — only the non-stream path trims)."""
        from transformers import TextIteratorStreamer

        budget = max_tokens or self.spec.max_new_tokens
        lock = self.lock
        lock.acquire()
        try:
            self.ensure_loaded()
            import torch

            prompt, input_len = self._prepare_inputs(messages)
            streamer = TextIteratorStreamer(
                self._tokenizer, skip_prompt=True,
                skip_special_tokens=True, timeout=_STREAM_TIMEOUT_S)
            kwargs: Dict[str, Any] = {
                "max_new_tokens": budget, "streamer": streamer}
            kwargs.update(self._sampling_kwargs(temperature, top_p))
            criteria = self._stop_criteria(stop)
            if criteria is not None:
                kwargs["stopping_criteria"] = [criteria]

            result: Dict[str, Any] = {}

            def generate() -> None:
                # The worker owns exact accounting: the generate() output
                # row length gives the true new-token count for usage and
                # the finish_reason; end() here (beside generate's own)
                # guarantees the reader unblocks even on a generate error.
                try:
                    with torch.inference_mode():
                        out = self._model.generate(**prompt, **kwargs)
                    result["new_tokens"] = (
                        int(out[0].shape[-1]) - input_len)
                except BaseException as exc:  # noqa: BLE001 — surfaced below
                    result["error"] = repr(exc)
                finally:
                    streamer.end()

            worker = threading.Thread(
                target=generate, name=f"lmh-gen-{self.model_id}",
                daemon=True)
            worker.start()
            try:
                for delta in streamer:
                    if delta:
                        yield {"type": "delta", "text": delta}
            finally:
                worker.join(timeout=_STREAM_TIMEOUT_S)
            if "error" in result:
                raise RuntimeError(
                    f"{self.model_id}: generation failed ({result['error']})")
            finish, usage = self._finish_and_usage(
                int(result.get("new_tokens", 0)), budget, "", input_len)
            self.touch()
            yield StreamDone(finish_reason=finish, usage=usage)
        finally:
            lock.release()

    # --------------------------------------------------- system one (jev)
    @staticmethod
    def _decision_prompt(state: Optional[Dict[str, Any]],
                         questions: Dict[str, Any],
                         media_count: int = 0
                         ) -> Tuple[List[Dict[str, Any]], List[int]]:
        """The System One request → (one lettered-answer user message,
        the image order). See the module docstring's slot-format
        caveat. With media, the state text's ``<image:N>`` placeholders
        become ``{"type": "image"}`` parts at their positions — the
        part order IS the returned image order (the caller's images
        list must follow it); media no placeholder references ride as
        parts after the text. Out-of-range indexes clamp to the last
        media entry. Pure — the unit probe asserts its shape without a
        model."""
        import json as _json
        import re as _re

        lines: List[str] = [
            "You are answering typed decision questions. For each "
            "question reply with ONLY the letter of the answer "
            "(or YES / NO). No explanations."]
        if state:
            lines.append("\n# State\n" + _json.dumps(
                state, ensure_ascii=False, default=str))
        for key, q in questions.items():
            lines.append(f"\n# Question {key} ({q.type}): {q.instructions}")
            if q.type == "noul":
                lines.append("Answer YES or NO.")
                continue
            if q.type == "choice":
                pairs = list((q.criteria or {}).items())
                lines.append("\n".join(
                    f"{_CHOICE_LETTERS[i]}. {desc}"
                    for i, (_label, desc) in enumerate(pairs)))
            elif q.type == "score":
                lines.append("Levels, first to last:")
                lines.append("\n".join(
                    f"{_CHOICE_LETTERS[i]}. {level}"
                    for i, level in enumerate(q.criteria or [])))
        text = "\n".join(lines)
        if not media_count:
            return [{"role": "user", "content": text}], []
        parts: List[Dict[str, Any]] = []
        image_order: List[int] = []
        referenced: set = set()
        cursor = 0
        for match in _re.finditer(r"<image:(\d+)>", text):
            head = text[cursor:match.start()]
            if head:
                parts.append({"type": "text", "text": head})
            index = min(int(match.group(1)), media_count - 1)
            parts.append({"type": "image"})
            image_order.append(index)
            referenced.add(index)
            cursor = match.end()
        tail = text[cursor:]
        if tail:
            parts.append({"type": "text", "text": tail})
        for index in range(media_count):
            if index not in referenced:
                parts.append({"type": "image"})
                image_order.append(index)
        return [{"role": "user", "content": parts}], image_order

    def _logits_after_prompt(self, messages: List[Dict[str, Any]],
                             images: Optional[List[Any]] = None
                             ) -> Tuple[List[float], int]:
        """ONE forward of the templated prompt → (the last-position
        logits, the prompt's token count — the usage block's input for
        that read). ``images`` ride the same forward (vision-conditioned
        read). Lock held by caller."""
        import torch

        prompt, input_len = self._prepare_inputs(messages, images)
        with torch.inference_mode():
            out = self._model(**prompt)
        return out.logits[0, -1, :].tolist(), int(input_len)

    def systemone(self, state: Optional[Dict[str, Any]],
                  media: Optional[List[Dict[str, Any]]],
                  questions: Dict[str, Any]
                  ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, int]]:
        """The System One contract: typed questions in, calibrated
        answers out — ``noul`` → ``{noul: p_yes}``, ``choice`` →
        ``{probabilities: {option: p}}``, ``score`` →
        ``{score: expected_level}``. ``media`` — (raw bytes, sniffed
        mime) pairs, decoded by the router — is conditioned on when the
        weights carry an image processor; a text-only load raises
        ``ValueError`` (the router maps capability gaps to the 400
        before dispatch). Everything runs under the model lock in ONE
        forward pass per question — the images ride EVERY question's
        forward (each answer's read is its own instructions conditioned
        on the same media). Returns ``(answers, usage)`` — the
        usage dict is real tokenizer counts (the templated decision
        prompt of each question's forward; one answer-token read per
        question) for the response's usage block."""
        images = self._decode_media(media)
        return self.run_locked(lambda: self._systemone_locked(
            state, questions, images))

    def _decode_media(self, media: Optional[List[Dict[str, Any]]]
                      ) -> List[Any]:
        """The router's (raw bytes, sniffed mime) pairs → PIL RGB
        images (the processor's input). ``ValueError`` on a
        non-decodable payload or a text-only load."""
        if not media:
            return []
        if not self.vision_ready:
            raise ValueError(
                f"{self.model_id}: media read on a text-only load "
                "(the weights carry no image processor)")
        from io import BytesIO

        from PIL import Image

        images: List[Any] = []
        for raw, _mime in media:
            try:
                images.append(Image.open(BytesIO(raw)).convert("RGB"))
            except Exception as exc:  # noqa: BLE001 — surfaced as a 400
                raise ValueError(
                    f"media payload is not a decodable image: {exc}") from exc
        return images

    def _systemone_locked(self, state: Optional[Dict[str, Any]],
                          questions: Dict[str, Any],
                          images: Optional[List[Any]] = None
                          ) -> Tuple[Dict[str, Dict[str, Any]],
                                     Dict[str, int]]:
        """One forward PER QUESTION, the answer read from that
        question's own last-position logits. (The single-forward-for-
        all variant read EVERY answer off the same row: all noul
        questions in a request returned one shared p_yes — the
        2026-11-11 self-test finding; a question's probability must be
        conditioned on its own instructions.)

        The choice/score read is over the LETTER tokens (A, B, C, ...)
        the prompt demands — NOT the label texts: the model is primed
        to emit the letter, and the 2026-11-12 repair-verdict probes
        showed the label-text read measures a distribution the model
        never intends (uniform conservatism where the letter read
        discriminates). Answer KEYS stay the labels — the wire contract
        is unchanged."""
        answers: Dict[str, Dict[str, Any]] = {}
        prompt_tokens = 0
        for key, q in questions.items():
            prompt_messages, image_order = self._decision_prompt(
                state, {key: q}, media_count=len(images or []))
            question_images = (
                [images[i] for i in image_order] if images else None)
            logits, input_len = self._logits_after_prompt(
                prompt_messages, question_images or None)
            prompt_tokens += input_len
            if q.type == "noul":
                labels: List[str] = list(_NOUL_LABELS)
                candidates = [_label_token_candidates(self._tokenizer,
                                                      label)
                              for label in labels]
            else:
                labels = (list((q.criteria or {}).keys())
                          if q.type == "choice"
                          else [str(c) for c in (q.criteria or [])])
                if not labels:
                    raise ValueError(
                        f"question `{key}` ({q.type}) carries no "
                        "criteria")
                candidates = [_label_token_candidates(self._tokenizer,
                                                      _CHOICE_LETTERS[i])
                              for i in range(len(labels))]
            probs = _softmax(_label_scores(logits, candidates))
            if q.type == "noul":
                answers[key] = {"noul": probs[0]}  # labels are (yes, no)
            elif q.type == "choice":
                answers[key] = {"probabilities": dict(zip(labels, probs))}
            else:  # score — the expected level over the ordered criteria
                expected = sum(i * p for i, p in enumerate(probs))
                answers[key] = {
                    "score": expected,
                    "level_probabilities": dict(zip(labels, probs)),
                }
        self.touch()
        completion_tokens = len(questions)
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        return answers, usage

    # --------------------------------------------------------------- status
    def _status_detail(self) -> dict:
        detail = super()._status_detail()
        detail.update({
            "path": str(self._model_dir),
            "resolved_device": self._resolved_device,
            "resolved_dtype": self._resolved_dtype,
            "loaded": self._model is not None,
            "vision_ready": self._processor is not None,
        })
        return detail
