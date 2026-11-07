"""GPUInfo — data definition.

Migrated verbatim from :mod:`_common.df_utils._detector` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GPUInfo:
    """Snapshot of GPU availability + capacity.

    Attributes:
        available: True iff a CUDA GPU + working cuDF + allocatable VRAM.
        device_name: GPU name (e.g. "NVIDIA GeForce RTX 5090") or "".
        driver_version: driver version string or "".
        vram_total_bytes: total VRAM in bytes (0 if unknown).
        vram_free_bytes:  free VRAM in bytes  (0 if unknown).
        cudf_version:     installed cuDF version or "" if not installed.
        reason:           when unavailable, a short human-readable reason.
    """
    available: bool
    device_name: str = ""
    driver_version: str = ""
    vram_total_bytes: int = 0
    vram_free_bytes: int = 0
    cudf_version: str = ""
    reason: str = ""
