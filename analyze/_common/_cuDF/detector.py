"""CUDA / GPU availability detector for analyze._common._cuDF.

Detects whether a CUDA-capable GPU is available for cuDF acceleration.
Detection is **lazy** (cuDF/cupy are imported only when first queried)
and **cached** (the result is memoized so repeated calls are free).

Detection layers (first failure stops the chain):
  0. **Environment gate** — GPU is only attempted on Linux or WSL.
     Native Windows / macOS / any other OS short-circuits to
     "unavailable" before any subprocess or import runs. This keeps
     developer machines on CPU (pandas) and avoids the cost of
     probing nvidia-smi where cuDF could never run anyway.
  1. ``nvidia-smi`` subprocess — confirms a driver + GPU is present
     without importing any heavy GPU library. Works even when cuDF is
     not installed.
  2. ``cudf`` import — confirms the cuDF package is installed.
  3. ``cudf.DataFrame()`` smoke test — confirms the CUDA runtime can
     actually allocate GPU memory (catches driver/runtime mismatches).

The detector also queries total + free VRAM so the router can reject
DataFrames that would not fit on the GPU.

All GPU-interacting code is behind the ``is_gpu_available()`` gate, so
importing this module on a CPU-only machine has zero side effects.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


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


def _is_linux_or_wsl() -> bool:
    """Return True iff the current OS is native Linux or WSL.

    WSL is detected via the Microsoft kernel release string in
    ``platform.release()`` (e.g. ``5.15.153.1-microsoft-standard-WSL2``)
    or the ``WSL_DISTRO_NAME`` environment variable.

    Native Windows (cygwin Python, pure Windows Python, etc.) returns
    False, which makes ``detect_gpu()`` short-circuit to "unavailable"
    before spending ~50ms on a doomed ``nvidia-smi`` probe and avoids
    importing cuDF where it could never run anyway.

    macOS also returns False (no NVIDIA CUDA drivers since 2018).
    """
    if platform.system() == "Linux":
        return True
    # WSL reports ``platform.system() == "Linux"`` too, but be defensive
    # in case of a Python build that doesn't expose uname the usual way.
    rel = platform.uname().release.lower()
    if "microsoft" in rel or "wsl" in rel:
        return True
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    return False


def _query_nvidia_smi() -> Optional[dict]:
    """Run ``nvidia-smi`` and return GPU metadata, or None on failure.

    Returns dict with keys: device_name, driver_version,
    vram_total_bytes, vram_free_bytes.
    """
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return None

    try:
        result = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=name,driver_version,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None

    # Expected: "NVIDIA GeForce RTX 5090, 610.47, 32607 MiB, 32103 MiB"
    # With nounits: "NVIDIA GeForce RTX 5090, 610.47, 32607, 32103"
    parts = [p.strip() for p in result.stdout.strip().split(",")]
    if len(parts) < 4:
        return None

    try:
        vram_total_mib = int(parts[2])
        vram_free_mib = int(parts[3])
    except ValueError:
        return None

    # MiB → bytes (1 MiB = 1024² bytes).
    mib_to_bytes = 1024 * 1024

    return {
        "device_name": parts[0],
        "driver_version": parts[1],
        "vram_total_bytes": vram_total_mib * mib_to_bytes,
        "vram_free_bytes": vram_free_mib * mib_to_bytes,
    }


def _try_import_cudf() -> Optional[str]:
    """Import cuDF and return its version, or None if not installed.

    Kept separate from the smoke test so the router can distinguish
    "cuDF not installed" (installable) from "cuDF installed but broken"
    (driver/runtime mismatch).
    """
    try:
        import cudf  # type: ignore[import-untyped]
        return getattr(cudf, "__version__", "unknown")
    except ImportError:
        return None
    except Exception:
        # cuDF installed but fails to import (e.g. CUDA runtime missing).
        return None


@lru_cache(maxsize=1)
def detect_gpu() -> GPUInfo:
    """Detect GPU availability + capacity. Result is cached.

    This is the single entry point for GPU detection. Callers should
    use ``is_gpu_available()`` or ``get_gpu_info()`` instead of calling
    this directly.

    The cache means the GPU is probed at most once per process. If the
    GPU state changes (e.g. driver crash), the process must be restarted.
    """
    # Step 0: environment gate. cuDF only runs on Linux/WSL; on native
    # Windows / macOS / any other OS we short-circuit to "unavailable"
    # before running nvidia-smi or importing cuDF. This keeps the CPU
    # path (pandas) on developer machines and avoids ~50ms of subprocess
    # probing where GPU acceleration could never work anyway.
    if not _is_linux_or_wsl():
        return GPUInfo(
            available=False,
            reason=f"non-Linux/WSL OS ({platform.system()}) — GPU bypassed",
        )

    # Step 1: nvidia-smi (no heavy imports — works without cuDF).
    smi = _query_nvidia_smi()
    if smi is None:
        return GPUInfo(
            available=False,
            reason="nvidia-smi not found or returned no GPU "
                   "(no NVIDIA driver / no GPU / WSL-CPU-only)",
        )

    # Step 2: cuDF import.
    cudf_version = _try_import_cudf()
    if cudf_version is None:
        return GPUInfo(
            available=False,
            device_name=smi["device_name"],
            driver_version=smi["driver_version"],
            vram_total_bytes=smi["vram_total_bytes"],
            vram_free_bytes=smi["vram_free_bytes"],
            reason="cuDF not installed (pip install cudf-cu12)",
        )

    # Step 3: smoke test — can cuDF actually allocate GPU memory?
    try:
        import cudf  # type: ignore[import-untyped]
        _ = cudf.DataFrame({"x": [1, 2, 3]})
        del _
    except Exception as e:
        return GPUInfo(
            available=False,
            device_name=smi["device_name"],
            driver_version=smi["driver_version"],
            vram_total_bytes=smi["vram_total_bytes"],
            vram_free_bytes=smi["vram_free_bytes"],
            cudf_version=cudf_version,
            reason=f"cuDF smoke test failed: {type(e).__name__}: {e}",
        )

    return GPUInfo(
        available=True,
        device_name=smi["device_name"],
        driver_version=smi["driver_version"],
        vram_total_bytes=smi["vram_total_bytes"],
        vram_free_bytes=smi["vram_free_bytes"],
        cudf_version=cudf_version,
    )


def is_gpu_available() -> bool:
    """Return True iff a working CUDA GPU + cuDF is available.

    Cached after the first call. Safe to call from any thread.
    """
    return detect_gpu().available


def get_gpu_info() -> GPUInfo:
    """Return the full GPUInfo snapshot (cached after first call)."""
    return detect_gpu()


def reset_cache() -> None:
    """Clear the cached GPU detection result.

    Only needed for tests that mock the GPU state. In production code
    the cache is never cleared — the GPU does not appear/disappear
    during a process lifetime.
    """
    detect_gpu.cache_clear()
