"""GPUDecision — data definition.

Migrated verbatim from :mod:`_common.df_utils._router` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from .gpu_info import GPUInfo


@dataclass(frozen=True)
class GPUDecision:
    """Result of ``should_use_gpu()``.

    Attributes:
        use_gpu: True iff GPU is available AND data is above breakeven.
        reason:  human-readable explanation of the decision.
        gpu_info: the GPUInfo snapshot (always populated).
        breakeven: the row threshold that was applied.
        est_vram_bytes: estimated VRAM needed (informational only).
    """
    use_gpu: bool
    reason: str
    gpu_info: GPUInfo
    breakeven: int
    est_vram_bytes: int
