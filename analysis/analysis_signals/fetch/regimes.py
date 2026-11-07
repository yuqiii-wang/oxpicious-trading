"""Market-regime daily states (analysis.analysis_signals.fetch.regimes).

Ported from the retired analysis.analysis_forecasts.fetch.regimes when
the forecast layer was removed 2026-10-05. The engine merges this onto
the input frame by (code, date); days with no row default to 'calm'
engine-side.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64
from _common.labels import SEC_TYPE_CODE
from _common.sql_views import render_sql_view

_REGIME_COLUMNS = ["code", "date", "regime"]


async def fetch_market_regimes(
    conn,
    sec_type: str,
    since: date,
) -> pd.DataFrame:
    """Fetch the sec_type's DAILY market-regime states bounded to
    date >= ``since`` — one row per (code, date) from
    stats.market_regimes (the 4-state label: calm / hot / panic /
    quiet; built by builds.market_regimes with shift-1 trailing inputs,
    so every label is known at its own day's close — no look-ahead).
    The registry's sec_type column stores its static.label_dim SMALLINT
    code (the label-code contract) — ``sec_type`` here is the label
    string and is bound as its code.

    Returns a DataFrame with columns ``_REGIME_COLUMNS`` (date as
    datetime64[us])."""
    # analysis/analysis_signals/fetch/regimes/market_regimes
    view = render_sql_view(
        "analysis/analysis_signals/fetch/regimes/market_regimes",
        sec_type=SEC_TYPE_CODE[sec_type],
        since=since,
    )
    rows = await conn.fetch(view.sql, *view.params)
    if not rows:
        return pd.DataFrame(columns=_REGIME_COLUMNS)
    df = pd.DataFrame(rec_cols(rows), columns=_REGIME_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    return df
