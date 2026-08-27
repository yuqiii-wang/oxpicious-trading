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

import math

import numpy as np
import pandas as pd


def nan_to_none(vals: list) -> list:
    """Post-emission NA sweep over a Python list: NaN/pd.NA/pd.NaT → None."""
    out = []
    for v in vals:
        if v is None or v is pd.NA or v is pd.NaT:
            out.append(None)
        elif isinstance(v, float):
            out.append(None if math.isnan(v) else v)
        else:
            out.append(v)
    return out


def records_from_frame(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    """Row dicts for DB upserts WITHOUT to_dict(orient="records").

    Deterministic dtypes: float columns arrive as Python float, bool as
    bool, Int64/object columns as their element types; date columns should
    be pre-normalized to datetime.date via dates_as_date_list.
    """
    if not cols:
        return []
    col_lists = [nan_to_none(np.asarray(df[c]).tolist()) for c in cols]
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
