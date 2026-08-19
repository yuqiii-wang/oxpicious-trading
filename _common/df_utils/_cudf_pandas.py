"""Process-level cudf.pandas activation (the single shared implementation).

cudf.pandas patches the ``pandas`` import so ALL subsequent pandas
operations transparently run on the GPU when beneficial. Because it is
an import-time hook, it MUST be enabled BEFORE pandas is first
imported — entry points therefore call
:func:`maybe_enable_cudf_pandas` at the very top, before importing any
pandas-dependent module.

This complements (not replaces) the op-level GPU convention
(``_common.df_utils.should_use_gpu``): that router decides per-operation
DataFrame placement inside library code, while this activates one
process-wide pandas backend — the only way to accelerate pandas code
deep inside library call paths without touching every call site.

Detection reuses the shared cached detector (``_detector.detect_gpu``:
nvidia-smi + cuDF import + GPU-allocation smoke test) — no duplicated
CUDA probing. Importing ``_common.df_utils`` is pandas-free (lazy PEP
562 exports), so this module is safe to use before the hook installs.

No hard dependency: no GPU / cuDF missing → CPU pandas, transparently.
"""
from __future__ import annotations

import sys


def maybe_enable_cudf_pandas(mode: str = "auto") -> tuple[bool, str]:
    """Enable cudf.pandas per ``mode`` — call BEFORE importing pandas.

    Modes:
      - "on":  try to enable; failure is reported but not raised.
      - "auto": enable only when a CUDA device is detected (default).
      - "off": never enable (pure CPU pandas).

    Both "on" and "auto" stay on CPU when the shared detector reports
    the GPU unavailable (no NVIDIA driver / cuDF missing / smoke-test
    failure) — "on" only skips the environment short-circuit on
    non-Linux/WSL hosts, never the hardware checks.

    Returns (enabled, description) — description is human-readable for
    the CLI startup banner.
    """
    if mode == "off":
        return False, "GPU disabled (mode=off)"
    if "cudf.pandas" in sys.modules:
        return True, "cudf.pandas already active"
    if "pandas" in sys.modules:
        # Too late for the import hook — report honestly and stay on CPU.
        return False, "pandas already imported; cudf.pandas not applied"

    # Shared cached detection: nvidia-smi → cuDF import → GPU allocation
    # smoke test. (Importing the detector does NOT import pandas.)
    from _common.df_utils._detector import detect_gpu

    info = detect_gpu()
    if not info.available:
        return False, f"CPU pandas ({info.reason})"

    try:
        import cudf.pandas  # noqa: F401  # activates the import hook
    except Exception as exc:
        return False, f"CPU pandas (cudf.pandas import failed: {exc})"

    vram_gb = info.vram_total_bytes / 1024**3
    return True, (
        f"cudf.pandas active (GPU pandas: {info.device_name}, "
        f"{vram_gb:.0f} GB VRAM, cuDF {info.cudf_version})"
    )


__all__ = ["maybe_enable_cudf_pandas"]
