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

import os
import sys


def _gpu_compute_smoke_test() -> tuple[int, int, list[str]]:
    """Run a tiny numeric groupby+merge+sort through cudf.pandas and count
    how many proxied calls took the GPU (fast) vs CPU (fallback) path.

    Diagnoses the silent-failure mode where the import hook is installed
    (GPU memory reserved by the CUDA context) but EVERY operation falls
    back to CPU pandas — the process then looks "GPU active" in nvidia-smi
    while doing zero GPU compute.

    Uses cudf.pandas' own Profiler (sys.settrace-based) so the counts are
    exact. Returns (n_gpu_calls, n_cpu_calls, cpu_func_names).
    """
    import pandas as pd  # proxied by cudf.pandas at this point
    from cudf.pandas.profiler import Profiler

    with Profiler() as prof:
        df = pd.DataFrame(
            {"k": ["a", "a", "b", "b", "c"], "v": [1.0, 2.0, 3.0, 4.0, 5.0]}
        )
        agg = df.groupby("k", as_index=False).agg(m=("v", "mean"))
        merged = agg.merge(agg, on="k")
        _ = float(merged.sort_values("m_x")["m_x"].sum())

    n_gpu = n_cpu = 0
    cpu_funcs: list[str] = []
    for func_name, calls in prof.per_function_stats.items():
        n_gpu += len(calls.get("gpu", []))
        n_cpu += len(calls.get("cpu", []))
        if calls.get("cpu"):
            cpu_funcs.append(func_name)
    return n_gpu, n_cpu, cpu_funcs


_MSG_MAX = 240


def _short(exception: Exception) -> str:
    """One-line exception message, capped at ``_MSG_MAX`` chars.

    Some cuDF errors embed the full arg list in the message (e.g.
    ``pandas.concat`` raising ``can only concatenate objects which are
    instances of {...} [then 1000x <class 'pandas.DataFrame'>]``) —
    printing it verbatim spams megabyte-long log lines.
    """
    msg = " ".join(str(exception).split())
    if len(msg) > _MSG_MAX:
        msg = msg[:_MSG_MAX] + f"... [truncated, {len(msg)} chars total]"
    return msg


def _patch_fallback_logger() -> None:
    """Replace cudf.pandas' log_fallback with a safe stdout printer.
    cudf 26.08's own ``log_fallback`` crashes with IndexError on property
    fallbacks (``df.columns`` etc.) where ``slow_args`` carries no args
    tuple — a crash INSIDE the fallback handler that kills the whole
    process. Our replacement never raises and prints one concise line
    per fast->slow fallback so blockers are visible in run logs.

    The proxy imports ``log_fallback`` lazily at each fallback
    (``from ._logger import log_fallback``), so patching the module
    attribute is effective for all subsequent fallbacks.
    """
    try:
        import cudf.pandas._logger as _cudf_logger
    except Exception:
        return

    def _safe_log_fallback(slow_args: tuple, slow_kwargs: dict,
                           exception: Exception) -> None:
        caller = slow_args[0] if slow_args else None
        name = getattr(caller, "__qualname__", None) or (
            getattr(caller, "__name__", None)
            if caller is not None else repr(caller)
        )
        module = getattr(caller, "__module__", "")
        full = f"{module}.{name}" if module else str(name)
        # Optional caller location (CUDF_FALLBACK_TRACE=1): first stack
        # frame outside cudf/pandas/proxy internals — the user code that
        # triggered the fast->slow fallback.
        where = ""
        if os.environ.get("CUDF_FALLBACK_TRACE") == "1":
            import inspect
            frame = inspect.currentframe()
            while frame is not None:
                f_name = frame.f_code.co_filename
                if ("cudf" not in f_name and "pandas" not in f_name
                        and "_cudf_pandas" not in f_name):
                    where = (
                        f" @ {os.path.basename(f_name)}:"
                        f"{frame.f_lineno}"
                    )
                    break
                frame = frame.f_back
        print(
            f"[cudf fallback] {full}{where}: "
            f"{type(exception).__name__}: {_short(exception)}",
            flush=True,
        )

    _cudf_logger.log_fallback = _safe_log_fallback


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

    # Log every fast->slow fallback to stdout (one "[cudf fallback] ..."
    # line each — see _patch_fallback_logger). The env var is read
    # dynamically by the proxy at each op, so setting it here — before
    # the first pandas op — is enough. Disable by exporting
    # LOG_FAST_FALLBACK=0.
    os.environ.setdefault("LOG_FAST_FALLBACK", "1")

    try:
        import cudf.pandas

        # cudf >= 25.x: importing the module does NOT install the import
        # hook — install() must be called explicitly (that's what the
        # `python -m cudf.pandas` launcher does). Without this call the
        # process holds GPU memory (CUDA context from the detector's
        # allocation smoke test) but every pandas op silently runs on
        # CPU — exactly "memory reserved, zero compute" in nvidia-smi.
        cudf.pandas.install()
        if not getattr(cudf.pandas, "LOADED", False):
            return False, (
                "CPU pandas (cudf.pandas.install() did not load the accelerator)"
            )
    except Exception as exc:
        return False, f"CPU pandas (cudf.pandas install failed: {exc})"

    _patch_fallback_logger()

    vram_gb = info.vram_total_bytes / 1024**3
    desc = (
        f"cudf.pandas active (GPU pandas: {info.device_name}, "
        f"{vram_gb:.0f} GB VRAM, cuDF {info.cudf_version})"
    )

    # Compute smoke test — distinguishes "hook installed" from "ops
    # actually running on GPU". If CPU(fallback) calls appear even for
    # this trivially cuDF-compatible workload, something environmental
    # is blocking GPU compute entirely (driver / CUDA context).
    try:
        n_gpu, n_cpu, cpu_funcs = _gpu_compute_smoke_test()
        if n_cpu == 0 and n_gpu > 0:
            desc += " | smoke test: GPU compute OK"
        else:
            desc += (
                f" | smoke test: {n_gpu} GPU / {n_cpu} CPU-fallback calls"
                f" (CPU funcs: {', '.join(sorted(set(cpu_funcs))[:8]) or 'none'})"
            )
    except Exception as exc:
        desc += f" | smoke test FAILED: {exc}"
    return True, desc


__all__ = ["maybe_enable_cudf_pandas"]
