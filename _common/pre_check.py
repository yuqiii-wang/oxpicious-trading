"""Resource pre-check guard for entry points.

Every ``__main__.py`` imports and runs :func:`pre_check` at the very top
(before any heavy import) so a doomed run exits immediately instead of
OOMing mid-pipeline:

- System RAM (WSL VM limit / host RAM when native) must be >= 36 GiB.
- NVIDIA GPU free VRAM (best device) must be >= 24 GiB.

Usage::

    from _common.pre_check import pre_check
    pre_check()

Override (for running a module concurrently with a GPU-resident
module): set the environment variable ``ANALYZE_MIN_GPU_GB`` (and/or
``ANALYZE_MIN_SYS_GB``) to lower the VRAM/RAM thresholds for that
process, e.g. ``ANALYZE_MIN_GPU_GB=2 python -m analyze.futures``.

Standalone module: stdlib-only, pandas/cudf-free, safe to call before
``_activate.activate()`` and before the first pandas import.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import Optional

MIN_SYS_GB: float = 36.0   # GiB of system memory required
MIN_GPU_FREE_GB: float = 24.0  # GiB of free VRAM on best GPU required

# Cooldown before the single retry when the first check fails — a
# concurrent finishing job may be about to release VRAM/RAM.
_COOLDOWN_SECS: float = 3.0

_GIB: float = 1024.0 ** 3


def _read_sys_mem_bytes() -> Optional[float]:
    """Total system memory in bytes, or None if undetectable.

    Linux/WSL: MemTotal from /proc/meminfo (reflects the WSL VM limit).
    Windows-native fallback: GlobalMemoryStatusEx via ctypes.
    """
    try:
        with open("/proc/meminfo", "r") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    # "MemTotal:       24703284 kB"
                    kb = float(line.split()[1])
                    return kb * 1024.0
    except OSError:
        pass
    if sys.platform == "win32":  # pragma: no cover - native Windows only
        try:
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return float(stat.ullTotalPhys)
        except Exception:
            return None
    return None


def _query_best_gpu_free_bytes() -> tuple[Optional[float], str]:
    """Free VRAM of the GPU with most free memory, plus its name.

    Returns (free_bytes_or_None, description). None means no usable
    nvidia-smi / no GPU detected — treated as a failure by pre_check().
    """
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return None, "nvidia-smi not found"
    try:
        result = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=name,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None, "nvidia-smi failed to run"
    if result.returncode != 0 or not result.stdout.strip():
        return None, "nvidia-smi returned no GPU data"

    best_free_mib = -1
    best_name = ""
    for line in result.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            free_mib = int(parts[-1])
        except ValueError:
            continue
        if free_mib > best_free_mib:
            best_free_mib = free_mib
            best_name = ", ".join(parts[:-1])
    if best_free_mib < 0:
        return None, "nvidia-smi output unparsable"
    return best_free_mib * 1024.0 * 1024.0, best_name


def _fmt_gib(n_bytes: Optional[float]) -> str:
    if n_bytes is None:
        return "unknown"
    return f"{n_bytes / _GIB:.1f} GiB"


def _env_override(name: str, default: float) -> float:
    """Read a float threshold override from the environment.

    ``ANALYZE_MIN_GPU_GB`` / ``ANALYZE_MIN_SYS_GB`` lower the
    corresponding pre-check threshold (concurrency with a GPU-resident
    module). Invalid values are ignored (default applies).
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    return val if val >= 0 else default


def check_only(
    min_sys_gb: float | None = None,
    min_gpu_free_gb: float | None = None,
    require_gpu: bool = True,
) -> list[str]:
    """Run both checks and return a list of human-readable FAIL lines.

    Empty list = all checks passed. Does not exit; used by pre_check().
    Thresholds default to the module constants, overridable via the
    ``ANALYZE_MIN_SYS_GB`` / ``ANALYZE_MIN_GPU_GB`` env vars.

    ``require_gpu=False`` skips the GPU check entirely — for entry
    points that never touch the GPU (pure DB/CSV I/O pipelines); even
    ``min_gpu_free_gb=0`` would fail on hosts where nvidia-smi is
    unavailable, which is wrong for GPU-less workloads.
    """
    if min_sys_gb is None:
        min_sys_gb = _env_override("ANALYZE_MIN_SYS_GB", MIN_SYS_GB)
    if min_gpu_free_gb is None:
        min_gpu_free_gb = _env_override("ANALYZE_MIN_GPU_GB",
                                        MIN_GPU_FREE_GB)
    failures: list[str] = []

    sys_bytes = _read_sys_mem_bytes()
    ok = sys_bytes is not None and sys_bytes >= min_sys_gb * _GIB
    print(f"[PRE-CHECK] system RAM : {_fmt_gib(sys_bytes)} "
          f"(required >= {min_sys_gb:g} GiB) → "
          f"{'OK' if ok else 'FAIL'}", flush=True)
    if not ok:
        failures.append(
            f"system RAM {_fmt_gib(sys_bytes)} < {min_sys_gb:g} GiB required"
        )

    if not require_gpu:
        print("[PRE-CHECK] GPU free VRAM: SKIPPED "
              "(require_gpu=False — pipeline does not use the GPU)",
              flush=True)
        return failures

    # If cudf.pandas is already active in THIS process, the in-process
    # RMM pool (~25 GiB on a 32 GB card) shows up as used VRAM against
    # any re-check — but it is our own reservation, not contention. Fresh
    # processes still perform the full check below (pre_check always runs
    # before activate()), so cross-process concurrency stays protected.
    if "cudf" in sys.modules:
        print("[PRE-CHECK] GPU free VRAM: SKIPPED "
              "(cudf.pandas already active in this process — in-process "
              "RMM pool reserved)", flush=True)
        return failures

    gpu_free, gpu_name = _query_best_gpu_free_bytes()
    ok = gpu_free is not None and gpu_free >= min_gpu_free_gb * _GIB
    detail = f" ({gpu_name})" if gpu_name else ""
    print(f"[PRE-CHECK] GPU free VRAM: {_fmt_gib(gpu_free)}{detail} "
          f"(required >= {min_gpu_free_gb:g} GiB) → "
          f"{'OK' if ok else 'FAIL'}", flush=True)
    if not ok:
        failures.append(
            f"GPU free VRAM {_fmt_gib(gpu_free)} < {min_gpu_free_gb:g} GiB "
            f"required ({gpu_name})"
        )

    return failures


def pre_check(
    min_sys_gb: float | None = None,
    min_gpu_free_gb: float | None = None,
    require_gpu: bool = True,
) -> None:
    """Entry-point guard: print the report and SystemExit(1) on failure.

    On first failure, wait ``_COOLDOWN_SECS`` and re-check once — a
    concurrent finishing job may be about to release VRAM/RAM. If the
    re-check also fails, exit(1).

    ``require_gpu=False`` skips the GPU VRAM check (GPU-less pipelines).
    """
    failures = check_only(
        min_sys_gb=min_sys_gb,
        min_gpu_free_gb=min_gpu_free_gb,
        require_gpu=require_gpu,
    )
    if failures:
        print(f"[PRE-CHECK] first check failed; retrying after "
              f"{_COOLDOWN_SECS:g}s cooldown...", flush=True)
        time.sleep(_COOLDOWN_SECS)
        failures = check_only(min_sys_gb=min_sys_gb, min_gpu_free_gb=min_gpu_free_gb)
        if failures:
            for msg in failures:
                print(f"[PRE-CHECK] ERROR: {msg}", file=sys.stderr, flush=True)
            raise SystemExit(1)


__all__ = ["pre_check", "check_only"]
