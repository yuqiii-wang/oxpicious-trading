"""Market-regime daily states (analyze.analysis_forecasts.fetch.regimes)."""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64

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

    The engines merge this onto the input frame by (code, date); days
    with no row (not in the regimes build's universe) default to
    'calm' engine-side. Replaces the retired fetch_hyped_episodes
    (episode spans from the dropped stats.mov_ave_market_hypes).

    Returns a DataFrame with columns ``_REGIME_COLUMNS`` (date as
    datetime64[us]).
    """
    rows = await conn.fetch(
        """
        SELECT m.code,
               extract(epoch from m.date)::float8 AS date,
               m.regime::text AS regime
        FROM stats.market_regimes m
        WHERE m.sec_type = $1
          AND m.date >= $2
        ORDER BY m.code, m.date ASC
        """,
        sec_type,
        since,
    )
    if not rows:
        return pd.DataFrame(columns=_REGIME_COLUMNS)
    df = pd.DataFrame(rec_cols(rows), columns=_REGIME_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    return df
