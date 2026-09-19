"""CASE-free SQL templates for the mov_rsi signal family.

Plain SELECTs only — no CASE/WHEN, no conditional expressions. The
bucket read fans the forecast bucket's trigger arrays out to a LONG
frame (one row per bucket trigger day) via LATERAL unnest, so every
column arrives as a plain scalar (arrays never cross into the frames);
the indicator-value read is a WIDE literal-column SELECT (the
window→column mapping happens in the engine's vectorized melt).

Numeric columns are cast ``::float8`` at the SQL source and dates
leave as ``extract(epoch ...)::float8`` (the repo's asyncpg/cudf.pandas
contract — no Decimal objects, no object date columns downstream).
"""
from __future__ import annotations

from analyze.analysis_forecasts.config import (
    RSI_WINDOWS,
    TABLE_FORECAST,
    TABLE_IDENTITIES,
    TABLE_MOV_RSI,
)

# ---------------------------------------------------------------------------
#  mov_rsi buckets (long: one row per bucket × trigger day)
# ---------------------------------------------------------------------------

MOV_RSI_BUCKET_COLUMNS = [
    "code", "stat_month", "rsi_window", "side", "is_market_hyped",
    "ave_change", "reverse_prob", "occurrence_count",
    "ave_next",
    "ave_5d", "max_5d", "min_5d",
    "ave_20d", "max_20d", "min_20d",
    "ave_60d", "max_60d", "min_60d",
    "trig_date", "trig_excess",
]
MOV_RSI_BUCKET_EPOCH_COLS = ("stat_month", "trig_date")

MOV_RSI_BUCKETS_SQL = f"""
    SELECT i.code,
           extract(epoch from i.stat_month)::float8 AS stat_month,
           m.rsi_window::int                        AS rsi_window,
           m.side,
           m.is_market_hyped,
           fmx.ave_change::float8                   AS ave_change,
           fmx.reverse_prob::float8                 AS reverse_prob,
           fmx.occurrence_count::float8             AS occurrence_count,
           qn.ave_next                              AS ave_next,
           q5.ave_5d                                AS ave_5d,
           q5.max_5d                                AS max_5d,
           q5.min_5d                                AS min_5d,
           q20.ave_20d                              AS ave_20d,
           q20.max_20d                              AS max_20d,
           q20.min_20d                              AS min_20d,
           q60.ave_60d                              AS ave_60d,
           q60.max_60d                              AS max_60d,
           q60.min_60d                              AS min_60d,
           extract(epoch from u.trig_date)::float8  AS trig_date,
           u.trig_excess::float8                    AS trig_excess
    FROM {TABLE_IDENTITIES} i
    JOIN {TABLE_MOV_RSI} m
      ON m.forecast_id = i.forecast_id
    CROSS JOIN LATERAL (
        SELECT f.ave_change, f.reverse_prob, f.occurrence_count
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id AND f.period = 'mixed'
        OFFSET 0
    ) fmx
    LEFT JOIN LATERAL (
        SELECT f.trigger_dates, f.trigger_excess
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id AND f.period = 'next'
        OFFSET 0
    ) fnx ON TRUE
    LEFT JOIN LATERAL unnest(fnx.trigger_dates, fnx.trigger_excess)
      AS u(trig_date, trig_excess) ON TRUE
    LEFT JOIN LATERAL (
        -- the quality periods' forward profiles (bucket-level
        -- columns for the final SignalQuality gate; 'next' has no
        -- close-based max/min — its max/min check is not applicable)
        SELECT f.ave_change::float8 AS ave_next
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id AND f.period = 'next'
        OFFSET 0
    ) qn ON TRUE
    LEFT JOIN LATERAL (
        SELECT f.ave_change::float8 AS ave_5d,
               f.max_change::float8 AS max_5d,
               f.min_change::float8 AS min_5d
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id AND f.period = '5d'
        OFFSET 0
    ) q5 ON TRUE
    LEFT JOIN LATERAL (
        SELECT f.ave_change::float8 AS ave_20d,
               f.max_change::float8 AS max_20d,
               f.min_change::float8 AS min_20d
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id AND f.period = '20d'
        OFFSET 0
    ) q20 ON TRUE
    LEFT JOIN LATERAL (
        SELECT f.ave_change::float8 AS ave_60d,
               f.max_change::float8 AS max_60d,
               f.min_change::float8 AS min_60d
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id AND f.period = '60d'
        OFFSET 0
    ) q60 ON TRUE
    WHERE i.sec_type = $1
      AND i.bucket = 'mov_rsi'
      AND i.stat_month = $2
      AND m.pct = $3::int
    ORDER BY i.code, m.rsi_window, m.side, u.trig_date
"""

# ---------------------------------------------------------------------------
#  mov_rsi indicator values (wide: one row per (code, date), one RSI
#  column per window — the engine melts)
# ---------------------------------------------------------------------------

MOV_RSI_VALUE_COLUMNS = ["code", "date"] + [
    f"rsi_{w}days" for w in RSI_WINDOWS
]

_MOV_RSI_SELECT_COLS = ",\n       ".join(
    f"r.rsi_{w}days::float8 AS rsi_{w}days" for w in RSI_WINDOWS
)

MOV_RSI_VALUES_SQL = f"""
    SELECT r.code,
           extract(epoch from r.date)::float8 AS date,
           {_MOV_RSI_SELECT_COLS}
    FROM analysis.mov_ave_rsi r
    WHERE r.sec_type = $1
      AND r.code = ANY($2::text[])
      AND r.date = ANY($3::date[])
"""

__all__ = [
    "MOV_RSI_BUCKET_COLUMNS",
    "MOV_RSI_BUCKET_EPOCH_COLS",
    "MOV_RSI_BUCKETS_SQL",
    "MOV_RSI_VALUE_COLUMNS",
    "MOV_RSI_VALUES_SQL",
]
