"""End-of-run memory release + report for entry points.

Mirror of ``_common.pre_check``: every ``__main__.py`` in builds/,
analyze/ and strategy/ calls :func:`post_check` as its FINAL step (in a
``finally`` so it also runs on exceptions / Ctrl-C / SystemExit) so a
finished pipeline reports and hands back the memory it borrowed:

- CPU: multi-generation ``gc.collect()`` to drop proxy/DataFrame
  objects, then report process RSS before -> after.
- GPU: release the CuPy pool (``free_all_blocks``, reuses
  ``release_cupy_pool`` — verified to return pooled blocks to the
  driver) and reset the RMM current resource, then report free VRAM
  before -> after via the same nvidia-smi query ``pre_check`` uses.

KNOWN LIMIT (probed 2026-08-30, temp_scripts/probe_rmm_release*.py):
when cudf.pandas is active it pre-reserves a giant RMM
``PoolMemoryResource`` at install time (``initial_pool_size = free
VRAM``, ~24 GiB on a 32 GB card) and that pool NEVER returns blocks to
the driver mid-process — no public RMM API shrinks it
(``rmm.reinitialize()`` / resource swap / ``del`` + ``gc`` all verified
ineffective in-process). The reservation is returned when the process
exits, which is immediately after this check in every entry point.
In-context ``memGetInfo`` therefore shows the reservation as "used" and
the report calls it out instead of treating it as a leak.

Best-effort by design: every release step is individually guarded so a
missing CuPy / RMM / nvidia-smi (or a CPU-only host) degrades to a
report-only run. Importing this module is pandas-free; heavy imports
(cupy / rmm / nvidia-smi subprocess) happen inside the functions, so
the pre-check -> activate() import-order contract is untouched.

Usage::

    if __name__ == "__main__":
        from _common.post_check import post_check
        try:
            asyncio.run(main())
        finally:
            post_check()
"""
from __future__ import annotations

import gc
import os
import shutil
import subprocess
import sys

_GIB: float = 1024.0 ** 3

# Report a WARNING when usage stays above these after the release pass.
WARN_RSS_GB: float = 8.0    # process RSS after release


def _fmt_gib(n_bytes: float | None) -> str:
    return "unknown" if n_bytes is None else f"{n_bytes / _GIB:.1f} GiB"


def _proc_rss_bytes() -> float | None:
    """Resident set size of THIS process in bytes, or None.

    Linux/WSL: VmRSS from /proc/self/status. Fallback: psutil (covers
    native Windows where /proc does not exist).
    """
    try:
        with open("/proc/self/status", "r") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) * 1024.0
    except OSError:
        pass
    try:
        import psutil

        return float(psutil.Process().memory_info().rss)
    except Exception:
        return None


def _gpu_free_bytes() -> tuple[float | None, str]:
    """Free VRAM of the GPU with most free memory + its name.

    Same query as ``_common.pre_check`` (device-wide, PID-independent —
    reliable under WSL where per-process compute-app PIDs are host-side
    and do not match ``os.getpid()``; NOTE the WSL shim's free-VRAM
    reading is static — it reflects host-side other-process usage but
    NOT this process's dynamic allocations).
    """
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return None, "nvidia-smi not found"
    try:
        result = subprocess.run(
            [nvidia_smi, "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None, "nvidia-smi failed to run"
    if result.returncode != 0 or not result.stdout.strip():
        return None, "nvidia-smi returned no GPU data"
    best_free_mib = -1.0
    for line in result.stdout.strip().splitlines():
        try:
            best_free_mib = max(best_free_mib, float(line.strip()))
        except ValueError:
            continue
    if best_free_mib < 0:
        return None, "nvidia-smi output unparsable"
    return best_free_mib * 1024.0 * 1024.0, ""


def _ctx_used_bytes() -> float | None:
    """Device memory used by THIS process's CUDA context, or None.

    In-context ``cudaMemGetInfo`` (via cupy) — the accurate per-process
    metric (nvidia-smi is static under WSL). Includes the fixed
    cudf.pandas RMM pre-reservation when that is active.
    """
    try:
        import cupy as cp

        free_b, total_b = cp.cuda.runtime.memGetInfo()
        return total_b - free_b
    except Exception:
        return None


def release_memory() -> dict[str, float | None]:
    """Force-release CPU + GPU memory and return the before/after stats.

    Returns a dict with keys ``rss_before / rss_after / gpu_free_before
    / gpu_free_after / ctx_used_before / ctx_used_after`` (bytes, or
    None when undetectable). Steps:

    1. ``gc.collect()`` — drop all unreachable proxy/DataFrame objects
       first (this is what actually frees cuDF tables held by dead
       cudf.pandas proxy Series/DataFrames back to the RMM pool).
    2. CuPy pool → driver (``release_cupy_pool``: free_all_blocks on
       the default + pinned pools) — verified to return pooled blocks
       to the driver when the pool has them.
    3. ``gc.collect()`` again, then reset the RMM current resource —
       destroys the pool when possible so its blocks go back to the
       driver (no-op for the cudf.pandas pre-reservation, see module
       docstring).
    4. ``gc.collect()`` last — compact anything the releases freed.
    """
    stats: dict[str, float | None] = {}
    stats["rss_before"] = _proc_rss_bytes()
    stats["gpu_free_before"], _ = _gpu_free_bytes()
    stats["ctx_used_before"] = _ctx_used_bytes()

    gc.collect()

    # CuPy pool (our rolling-corr / FFT kernels) -> back to the driver.
    try:
        from _common.df_utils.rolling_corr import release_cupy_pool

        release_cupy_pool()
    except Exception:
        pass

    # Reset the RMM current resource -> destroy the pool if possible.
    gc.collect()
    try:
        import rmm

        current = rmm.mr.get_current_device_resource()
        rmm.mr.set_current_device_resource(rmm.mr.CudaMemoryResource())
        del current  # drop the last reference -> blocks freed
    except Exception:
        pass

    gc.collect()

    stats["rss_after"] = _proc_rss_bytes()
    stats["gpu_free_after"], _ = _gpu_free_bytes()
    stats["ctx_used_after"] = _ctx_used_bytes()
    return stats


def post_check() -> dict[str, float | None]:
    """Entry-point final step: force-release memory, print the report.

    Prints a CPU line (RSS before -> after, with released delta) and a
    GPU line (device-wide free VRAM before -> after, plus this
    process's in-context usage). A release is flagged with WARNING
    lines when RSS stays above ``WARN_RSS_GB`` after the pass, or free
    VRAM dropped during the release pass (another process allocating).
    The cudf.pandas fixed RMM pre-reservation is reported as such — it
    returns to the driver at process exit, which is the very next thing
    that happens in every entry point.
    """
    stats = release_memory()

    rss_b, rss_a = stats["rss_before"], stats["rss_after"]
    gpu_b, gpu_a = stats["gpu_free_before"], stats["gpu_free_after"]
    ctx_b, ctx_a = stats["ctx_used_before"], stats["ctx_used_after"]

    if rss_a is not None:
        released = (rss_b - rss_a) if rss_b is not None else None
        delta = (
            f" (released {_fmt_gib(released)})" if released and released > 0
            else ""
        )
        print(f"[POST-CHECK] CPU RSS : {_fmt_gib(rss_b)} -> "
              f"{_fmt_gib(rss_a)}{delta}", flush=True)
        if rss_a > WARN_RSS_GB * _GIB:
            print(f"[POST-CHECK] WARNING: RSS still above "
                  f"{WARN_RSS_GB:g} GiB after release (live reference "
                  f"or other process?)", flush=True)

    if ctx_b is not None and ctx_a is not None:
        cudf_note = ""
        if "cudf.pandas" in sys.modules:
            cudf_note = (" [cudf.pandas pre-reservation included; "
                         "returns at process exit]")
        print(f"[POST-CHECK] GPU in-context used: {_fmt_gib(ctx_b)} -> "
              f"{_fmt_gib(ctx_a)}{cudf_note}", flush=True)

    if gpu_b is not None and gpu_a is not None:
        returned = gpu_a - gpu_b
        delta = (
            f" (returned {_fmt_gib(returned)} to driver)"
            if returned > 0 else ""
        )
        print(f"[POST-CHECK] GPU free VRAM: {_fmt_gib(gpu_b)} -> "
              f"{_fmt_gib(gpu_a)}{delta}", flush=True)
        if returned < 0:
            print(f"[POST-CHECK] WARNING: free VRAM dropped "
                  f"{_fmt_gib(-returned)} during release — another "
                  f"process may be allocating on the GPU", flush=True)

    return stats


__all__ = ["release_memory", "post_check"]
