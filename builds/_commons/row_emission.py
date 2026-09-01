"""Cudf-safe DB row emission helpers shared by all builds.* modules.

Ported from builds/stock/_helpers/helpers.py so etf/index/options/futures
builds emit DB rows without cudf.pandas fallbacks:

* NEVER itertuples / iterrows / to_dict(orient="records") for DB rows —
  under cudf.pandas each element extraction is one slow-path fallback
  (~7,891 lines/run in stock builds before the fix). Instead: ONE numpy
  array per column (single explicit host transfer), then .tolist()
  converts every element to plain Python types in C, and row dicts are
  assembled by zip.
* Every emitted column passes through :func:`nan_to_none` — asyncpg
  writes Python float('nan') into a NUMERIC column as numeric-NaN (NOT
  NULL), which poisons downstream IS NULL checks. pandas .where(cond,
  None) does NOT reliably produce None on float dtype, so the sweep runs
  over the emitted Python lists (pure host-side).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# Hoisted proxy-attribute constants — NEVER access pd.NA / pd.NaT inside a
# per-element loop: under cudf.pandas `pd` is a proxy module, so every
# attribute lookup goes through getattr_real_or_wrapped (module_accelerator),
# which internally constructs/hashes pathlib.Path objects. 13M elements × 2
# lookups ≈ 310s of pure proxy overhead (py-spy, 2026-08-28 ETF run).
_NA = pd.NA
_NAT = pd.NaT


def _sweep_float(a: np.ndarray) -> np.ndarray:
    """Vectorized NaN→None for float columns (no per-element Python loop)."""
    out = a.astype(object)
    out[np.isnan(a)] = None
    return out


def nan_to_none(vals: list) -> list:
    """Post-emission NA sweep over a Python list: NaN/pd.NA/pd.NaT → None."""
    out = []
    append = out.append
    for v in vals:
        if v is None or v is _NA or v is _NAT:
            append(None)
        elif isinstance(v, float):
            append(None if v != v else v)
        else:
            append(v)
    return out


def records_from_frame(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    """Row dicts for DB upserts WITHOUT to_dict(orient="records").

    Deterministic dtypes: float columns arrive as Python float, bool as
    bool, Int64/object columns as their element types; date columns should
    be pre-normalized to datetime.date via dates_as_date_list.
    """
    if not cols:
        return []
    col_lists: list[list] = []
    for c in cols:
        a = np.asarray(df[c])
        if a.dtype.kind == "f":  # float32/float64 — vectorized sweep
            col_lists.append(_sweep_float(a).tolist())
        else:  # object/bool/int/nullable-dtypes — loop with hoisted constants
            col_lists.append(nan_to_none(a.tolist()))
    return [dict(zip(cols, vals)) for vals in zip(*col_lists)]


def dates_as_date_list(series: pd.Series) -> list:
    """datetime64 / ISO-string column → list of Python datetime.date.

    One numpy transfer, no Series.tolist()/itertuples/to_dict (each of
    those is one cudf.pandas slow-path fallback per element).
    """
    a = np.asarray(series)
    if a.size == 0:
        return []
    if a.dtype.kind == "M":  # datetime64[ns] → date objects
        return a.astype("datetime64[D]").astype(object).tolist()
    if a.dtype == object:
        from datetime import date as _date, datetime as _datetime

        out: list = []
        for v in a.tolist():
            if isinstance(v, str):
                d = _date.fromisoformat(str(v)[:10])
            elif isinstance(v, _datetime):
                d = v.date()
            else:  # already a date (or unexpected — pass through)
                d = v
            out.append(d)
        return out
    raise TypeError(f"dates_as_date_list: unsupported dtype {a.dtype}")
