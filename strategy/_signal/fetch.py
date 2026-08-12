"""DB fetch for the MA-spread signal layer.

Builds the SQL JOIN of
  - analysis.mov_ave_spreads_detail (MA-gap / slope / σ / turnover-MA columns)
  - analysis.mov_ave_rsi            (Wilder RSI + short-term gaps)
  - stats.<sec_type>_basic_stats    (OHLC for fill prices)

This is the "check what data to read" step of the signal layer: the column
lists in _signal.config declare the MA-trading requirements, and this
module materializes them into a per-(code, date) DataFrame.

Discovery + trade_decision reads live in strategy._common.fetch.
"""
from __future__ import annotations

import pandas as pd

from strategy._signal.config import (
    SIGNAL_COLUMNS,
    DETAIL_SIGNAL_COLUMNS,
    RSI_SIGNAL_COLUMNS,
    SEC_TYPE_BASIC_STATS_TABLE,
)


def _build_fetch_sql(sec_type: str) -> str:
    """Build the signal-fetch SQL for the given sec_type.

    Joins analysis.mov_ave_spreads_detail (d) + analysis.mov_ave_rsi (r) on
    (sec_type, code, date), then LEFT JOINs the per-sec_type basic_stats
    table (b) for OHLC. The LEFT JOIN on basic_stats means a row with a
    missing open price still survives (fill will be skipped in the
    backtest if OHLC is NULL on the fill date).

    NOTE: peaks_and_floors_date is deliberately NOT selected — it carries
    look-ahead bias (belt detection extends into the future).
    """
    basic_stats = SEC_TYPE_BASIC_STATS_TABLE[sec_type]
    detail_cols_sql = ",\n    ".join(f"d.{c}" for c in DETAIL_SIGNAL_COLUMNS)
    rsi_cols_sql = ",\n    ".join(f"r.{c}" for c in RSI_SIGNAL_COLUMNS)
    return f"""
        SELECT
            d.sec_type,
            d.code,
            d.date,
            {detail_cols_sql},
            {rsi_cols_sql},
            b.open  AS open_price,
            b.high  AS high_price,
            b.low   AS low_price,
            b.close AS close_price
        FROM analysis.mov_ave_spreads_detail d
        JOIN analysis.mov_ave_rsi r
            ON r.sec_type = d.sec_type
           AND r.code = d.code
           AND r.date = d.date
        LEFT JOIN {basic_stats} b
            ON b.code = d.code
           AND b.date = d.date
        WHERE d.sec_type = $1
          AND d.code = ANY($2::text[])
        ORDER BY d.code, d.date ASC
    """


async def fetch_signal_data(conn, sec_type: str, codes: list) -> pd.DataFrame:
    """Fetch the per-(code, date) signal + fill-price series for the given
    sec_type and code list.

    Returns a DataFrame sorted by (code, date) with columns:
        sec_type, code, date,
        <SIGNAL_COLUMNS...>,
        rsi_6days, rsi_10days, rsi_14days, rsi_20days,
        gap_2days, gap_3days,
        date_of_last_extreme, days_since_last_extreme, gap_since_last_extreme,
        open_price, high_price, low_price, close_price

    Decimal (NUMERIC) values are coerced to float for pandas arithmetic.
    The date column is normalized to python date for clean serialization.
    """
    if not codes:
        return pd.DataFrame()
    sql = _build_fetch_sql(sec_type)
    rows = await conn.fetch(sql, sec_type, sorted(codes))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    numeric_cols = list(SIGNAL_COLUMNS) + [
        "rsi_6days", "rsi_10days", "rsi_14days", "rsi_20days",
        "gap_2days", "gap_3days", "days_since_last_extreme",
        "gap_since_last_extreme", "open_price", "high_price", "low_price", "close_price",
    ]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df
