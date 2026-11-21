"""server_cluster.llm.backends — the per-kind serving backends
and the spec→backend factory (:func:`build_backend`).

Kinds: ``dummy`` (the offline provider simulation), ``tokenization``
(the StructBERT word segmenter) and ``jev`` (a Jev / System One
decision model — OneJev fine-tunes on transformers)."""
from __future__ import annotations

from ..config import ModelBackendSpec
from .base import ModelBackend
from .dummy import DummyChatBackend
from .jev import JevBackend
from .tokenization import TokenizationBackend

_BACKEND_CLASSES = {
    "dummy": DummyChatBackend,
    "tokenization": TokenizationBackend,
    "jev": JevBackend,
}


def build_backend(spec: ModelBackendSpec,
                  default_idle_offload_s: float,
                  default_idle_unload_s: float) -> ModelBackend:
    """Spec → backend instance. Construction is LIGHT (weights load
    lazily on first use) — the factory may run at host startup before
    any model is on the GPU; unknown kinds raise (config typo)."""
    cls = _BACKEND_CLASSES.get(spec.kind)
    if cls is None:
        raise ValueError(
            f"unknown backend kind {spec.kind!r} for model "
            f"{spec.model_id!r} — known: {sorted(_BACKEND_CLASSES)}")
    return cls(spec, default_idle_offload_s, default_idle_unload_s)


__all__ = [
    "DummyChatBackend", "JevBackend", "ModelBackend", "TokenizationBackend",
    "build_backend",
]
