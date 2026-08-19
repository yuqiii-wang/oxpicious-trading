"""One-liner cudf.pandas activation for entry points.

Every ``__main__.py`` in builds/, analyze/, strategy/ that uses
pandas for heavy compute should call :func:`activate` at the very top
(before any module that imports pandas) to ensure the cudf.pandas
import hook is installed before pandas' first import.

Usage::

    # At the very top of any entry point:
    from _common.df_utils._activate import activate
    activate()

This is equivalent to::

    from _common.df_utils import maybe_enable_cudf_pandas
    _on, _why = maybe_enable_cudf_pandas("auto")
    print(f"[gpu] {_why}", flush=True)

The helper exists so entry points don't need to remember the two-step
dance (import + call + print) and so the ``"auto"`` mode (GPU detection
with graceful CPU fallback) stays the project default.

The helper is PANDAS-FREE: importing it does NOT import pandas. The
lazy PEP 562 exports in ``_common/df_utils/__init__.py`` ensure
``maybe_enable_cudf_pandas`` is resolved lazily on first use only.
"""
from __future__ import annotations


def activate(mode: str = "auto") -> bool:
    """Activate cudf.pandas and print the result banner.

    Args:
        mode: "auto" (default), "on", or "off". See
            ``_cudf_pandas.maybe_enable_cudf_pandas``.

    Returns:
        True iff cudf.pandas was successfully enabled.
    """
    from _common.df_utils import maybe_enable_cudf_pandas

    enabled, desc = maybe_enable_cudf_pandas(mode)
    print(f"[gpu] {desc}", flush=True)
    return enabled


__all__ = ["activate"]
