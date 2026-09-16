"""Market-hype episodes (analyze.analysis_forecasts.fetch.hype)."""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64

_HYPER_COLUMNS = ["code", "start_date", "end_date"]


async def fetch_hyped_episodes(
    conn,
    sec_type: str,
    since: date,
) -> pd.DataFrame:
    """Fetch the sec_type's market-hype EPISODES (compact — one row per
    episode, not per date) bounded to end_date >= ``since``.

    An episode is a concatenated hype span (start_date..end_date
    inclusive, any min_checkin_period) from stats.mov_ave_market_hypes.
    Dates inside an episode are the "market-hyped dates"; expansion to
    per-(code, date) flags happens host-side in wide.build_hype_matrix
    (expanding in SQL to calendar rows would blow up row counts for the
    stock universe).

    Returns a DataFrame with columns ``_HYPER_COLUMNS`` (dates as
    datetime64[us]).
    """
    rows = await conn.fetch(
        """
        SELECT m.code,
               extract(epoch from m.start_date)::float8 AS start_date,
               extract(epoch from m.end_date)::float8   AS end_date
        FROM stats.mov_ave_market_hypes m
        WHERE m.sec_type = $1
          AND m.end_date >= $2
        ORDER BY m.code, m.start_date ASC
        """,
        sec_type,
        since,
    )
    if not rows:
        return pd.DataFrame(columns=_HYPER_COLUMNS)
    df = pd.DataFrame(rec_cols(rows), columns=_HYPER_COLUMNS)
    df["start_date"] = epoch_col_to_dt64(df["start_date"], index=df.index)
    df["end_date"] = epoch_col_to_dt64(df["end_date"], index=df.index)
    return df
