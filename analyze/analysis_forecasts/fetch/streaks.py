"""High/Low band-break excursion streaks (analyze.analysis_forecasts
.fetch.streaks).

The high_low_streaks family's source of truth:
analysis.mov_ave_high_low_pct_streaks (the mov_ave_spread analysis's
own streak table — the streaks are READ directly, no recomputation; run
python -m analyze.mov_ave_spread first). The streak table has no side
column, so the side is derived in SQL off the UNROUNDED end-date close
vs the end month's stored band.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64

_STREAK_SOURCE_COLUMNS = [
    "code", "period", "pct_type", "start_date", "end_date",
    "day_count", "side", "band_high", "band_low",
]

# End-date price source per sec_type — the SAME price convention as the
# input frame (_sources.PRICE_SOURCE re-aliased to the end-date join),
# because the streak side is read off the UNROUNDED close on end_date vs
# the stored (2dp) band: the exact test the streaks step used. Comparing
# the streak row's stored 2dp-rounded close instead is ambiguous for
# ~36% of ETF rows (small adjusted prices round into ties with the band).
_END_PRICE_SOURCE = {
    "index": ("stats.index_basic_stats eb", "eb.close"),
    "etf": (
        "stats.etf_basic_stats eb "
        "LEFT JOIN stats.etf_adjustment ea "
        "ON ea.code = eb.code AND ea.date = eb.date",
        "COALESCE(ea.adj_close, eb.close)",
    ),
    "stock": ("stats.stock_basic_stats eb", "eb.close"),
}


async def fetch_high_low_streaks(
    conn,
    sec_type: str,
    codes: list[str],
    since: date,
) -> pd.DataFrame:
    """Fetch the sec_type's band-break excursion streaks from
    analysis.mov_ave_high_low_pct_streaks, bounded to end_date >= ``since``
    (a streak whose end precedes the earliest window start can never
    anchor inside it), with each streak's SIDE derived in SQL.

    The streaks table has no side column; the END date is out-of-band by
    construction, so the side is read off the UNROUNDED close on
    end_date (the end-date price join) vs the end date's OWN month band
    in analysis.mov_ave_high_low_pct: 'top' when close > high_val (an
    above-band excursion), 'bottom' when close < low_val; a tie falls to
    the NEARER band. Streaks whose band row or end-date price row is
    missing (none in practice — both derive from the same base rows the
    streaks step joined) drop out of the inner joins.

    Returns a DataFrame with columns ``_STREAK_SOURCE_COLUMNS``
    (band_high / band_low = the end month band's high_val / low_val —
    the streak side's threshold in PRICE space, consumed by the
    signals family as signal_threshold; dates as datetime64[us]),
    sorted by (code, start_date, period, pct_type).
    """
    if not codes:
        return pd.DataFrame(columns=_STREAK_SOURCE_COLUMNS)
    end_base, end_price = _END_PRICE_SOURCE[sec_type]
    rows = await conn.fetch(
        f"""
        SELECT s.code,
               s.period, s.pct_type, s.day_count,
               extract(epoch from s.start_date)::float8 AS start_date,
               extract(epoch from s.end_date)::float8   AS end_date,
               b.high_val::float8 AS band_high,
               b.low_val::float8  AS band_low,
               CASE
                   WHEN ep.price > b.high_val THEN 'top'
                   WHEN ep.price < b.low_val  THEN 'bottom'
                   WHEN abs(ep.price - b.high_val)
                        <= abs(ep.price - b.low_val) THEN 'top'
                   ELSE 'bottom'
               END AS side
        FROM analysis.mov_ave_high_low_pct_streaks s
        JOIN analysis.mov_ave_high_low_pct b
             ON b.sec_type = s.sec_type AND b.code = s.code
            AND b.date_year_month = date_trunc('month', s.end_date)::date
            AND b.period = s.period AND b.pct_type = s.pct_type
        JOIN LATERAL (
            SELECT {end_price}::float8 AS price
            FROM {end_base}
            WHERE eb.code = s.code AND eb.date = s.end_date
              AND eb.close IS NOT NULL
            LIMIT 1
        ) ep ON TRUE
        WHERE s.sec_type = $1
          AND s.code = ANY($2::text[])
          AND s.end_date >= $3
        ORDER BY s.code, s.start_date, s.period, s.pct_type
        """,
        sec_type,
        sorted(codes),
        since,
    )
    if not rows:
        return pd.DataFrame(columns=_STREAK_SOURCE_COLUMNS)
    df = pd.DataFrame(rec_cols(rows), columns=_STREAK_SOURCE_COLUMNS)
    df["start_date"] = epoch_col_to_dt64(df["start_date"], index=df.index)
    df["end_date"] = epoch_col_to_dt64(df["end_date"], index=df.index)
    return df
