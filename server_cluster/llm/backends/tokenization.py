"""server_cluster.llm.backends.tokenization — the StructBERT
Chinese word segmenter as a managed model (kind ``tokenization``).

The tokenization LLM served as request/response: this backend is a THIN
wrapper around the in-process engine ``_common.tokenization.
ChineseWordSegmenter`` — constructed here in MANAGED mode
(``use_host=False`` so the engine never HTTP-calls THIS host back into
itself; ``managed=True`` so its internal idle-offload watchdog stays
off) and driven by the manager's lifecycle exactly like every other
model. The engine keeps the forward+decode machinery (bit-identical to
the pre-host in-process path by construction — it IS that path), its
own per-call batching and the bf16/CUDA-fallback logic; the module's
docstring documents the segmentation behavior and its post-processing
tiers.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..config import ModelBackendSpec
from .base import ModelBackend

logger = logging.getLogger(__name__)


class TokenizationBackend(ModelBackend):
    """The ``word-segmenter-zh`` model (kind ``tokenization``) —
    segmentation batches in, word lists out (see :meth:`segment_batch`)."""

    def __init__(self, spec: ModelBackendSpec,
                 default_idle_offload_s: float,
                 default_idle_unload_s: float) -> None:
        super().__init__(spec, default_idle_offload_s,
                         default_idle_unload_s)
        from _common.tokenization import ChineseWordSegmenter

        model_dir = spec.resolved_path()
        if model_dir is None:
            raise ValueError(f"{self.model_id}: the tokenization kind "
                             "requires a model path in the config")
        self._engine = ChineseWordSegmenter(
            model_dir=model_dir,
            device=spec.device,
            aggressive=spec.aggressive,
            idle_offload_s=self.idle_offload_s,
            batch_size=spec.batch_size,
            use_host=False,   # the engine SERVES here — never self-calls
            managed=True,     # the manager's watchdog drives offload
        )

    # ------------------------------------------------------------ lifecycle
    def _load(self) -> None:
        self._engine.load()

    def _offload(self) -> None:
        self._engine.offload()

    def _unload(self) -> None:
        self._engine.unload()

    def _warmup(self) -> None:
        # One real forward so CUDA kernels/cuDNN autotune land before the
        # first request (the engine's lazy load already ran via _load).
        self._engine.segment_batch(["本地模型宿主预热"])

    # ------------------------------------------------------------ inference
    def segment_batch(self, texts: list[str],
                      batch_size: Optional[int] = None) -> list[list[str]]:
        """Texts -> word lists (order preserved; non-Chinese pass
        through as ``[text]`` — the engine's contract, so this endpoint
        matches the in-process ``ChineseWordSegmenter`` byte for byte)."""
        return self.run_locked(
            lambda: self._engine.segment_batch(texts, batch_size=batch_size))

    # --------------------------------------------------------------- status
    def _status_detail(self) -> dict:
        detail = super()._status_detail()
        detail.update(self._engine.status())
        return detail
