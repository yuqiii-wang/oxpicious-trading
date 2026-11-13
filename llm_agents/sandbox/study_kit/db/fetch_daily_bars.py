"""study_kit.db.fetch_daily_bars — daily bars for ONE code over a window.

Daily OHLCV over the reconstructed views
(database/sql/stats/99_reconstruct_views.sql), newest last, with the
precomputed MA5/20/60/120/252 and EMA12/26 columns — reuse them instead
of recomputing MAs when they cover the window.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from study_kit.db._shared import (
    DAILY_VIEWS,
    _DAILY_PROJECTIONS,
    require_sec_type,
)
from study_kit.db.query import query


def fetch_daily_bars(
    sec_type: str,
    code: str,
    start: str,
    end: Optional[str] = None,
    adjusted: bool = False,
) -> pd.DataFrame:
    """Daily bars for ONE code over [start, end] (inclusive 'YYYY-MM-DD'
    strings; end defaults to the table's max date), newest last, with
    the precomputed MA5/20/60/120/252 and EMA12/26 columns — reuse them
    instead of recomputing MAs when they cover the window.

    ``adjusted=True`` swaps the ETF price columns to the adjusted set
    (adj_open/adj_high/adj_low/adj_close) — the study-safe prices for a
    total-return view. ``code`` is the plain 6-digit form ('000001' SSE
    Composite, no exchange suffix). Identity rows without a bar (the
    views LEFT JOIN identity over every covered calendar day) are
    dropped — the result holds BARS only, so rolling windows never
    swallow a NULL close.

    Warmup: pull extra history BEFORE the studied window when the
    script computes its own rolling stats (MA252 needs ~1 extra year;
    index history starts 2020-01-01).
    """
    view = require_sec_type(sec_type, DAILY_VIEWS)
    projection = _DAILY_PROJECTIONS[sec_type]
    if adjusted and sec_type == "etf":
        projection = projection.replace(
            "open, high, low, close, adj_close",
            "adj_open AS open, adj_high AS high, adj_low AS low, "
            "adj_close AS close",
        ).replace(" adj_close,", "")
    conditions = "code = %s AND date >= %s AND close IS NOT NULL"
    params: list[Any] = [code, start]
    if end is not None:
        conditions += " AND date <= %s"
        params.append(end)
    sql = (
        f"SELECT {projection} FROM {view} WHERE {conditions} "
        "ORDER BY date ASC"
    )
    df = query(sql, params)
    if df.empty:
        raise ValueError(
            f"no bars for {sec_type} {code} over [{start}, {end}] — "
            "check the code (plain 6-digit form) and the range")
    return df
