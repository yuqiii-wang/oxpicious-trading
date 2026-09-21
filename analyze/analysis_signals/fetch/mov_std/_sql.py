"""CASE-free SQL templates for the mov_std signal family.

Plain SELECTs only — no CASE/WHEN, no conditional expressions. The
bucket read fans the forecast bucket's trigger arrays out to a LONG
frame (one row per bucket trigger day) via LATERAL unnest, so every
column arrives as a plain scalar (arrays never cross into the frames);
the indicator-value read is a WIDE close-value SELECT.

Numeric columns are cast ``::float8`` at the SQL source and dates
leave as ``extract(epoch ...)::float8`` (the repo's asyncpg/cudf.pandas
contract — no Decimal objects, no object date columns downstream).
"""
from __future__ import annotations

from analyze.analysis_forecasts.config import (
    TABLE_FORECAST,
    TABLE_IDENTITIES,
    TABLE_MOV_STD,
)
from analyze.analysis_forecasts.fetch._sources import AMT_SOURCE, PRICE_SOURCE

# ---------------------------------------------------------------------------
#  mov_std buckets (long: one row per bucket × trigger day). k_str is
#  the σ multiple in the float8::text rendering ("2", "2.5") — EXACTLY
#  the fragment the UI tick join concatenates into signal_sub_type.
# ---------------------------------------------------------------------------

MOV_STD_BUCKET_COLUMNS = [
    "code", "stat_month", "ma_window", "k", "k_str", "side", "regime_state",
    "ave_change", "reverse_prob", "occurrence_count",
    "ave_next",
    "ave_5d", "max_5d", "min_5d",
    "ave_20d", "max_20d", "min_20d",
    "trig_date", "trig_excess",
]
MOV_STD_BUCKET_EPOCH_COLS = ("stat_month", "trig_date")

MOV_STD_BUCKETS_SQL = f"""
    SELECT i.code,
           extract(epoch from i.stat_month)::float8 AS stat_month,
           m.ma_window::int                         AS ma_window,
           m.k::float8                              AS k,
           m.k::float8::text                        AS k_str,
           m.side,
           m.regime_state,
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
           extract(epoch from u.trig_date)::float8  AS trig_date,
           u.trig_excess::float8                    AS trig_excess
    FROM {TABLE_IDENTITIES} i
    JOIN {TABLE_MOV_STD} m
      ON m.forecast_id = i.forecast_id
    CROSS JOIN LATERAL (
        SELECT f.ave_change, f.reverse_prob, f.occurrence_count
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id AND f.period = 'mixed' AND f.delay = 0
        OFFSET 0
    ) fmx
    LEFT JOIN LATERAL (
        SELECT f.trigger_dates, f.trigger_excess
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id AND f.period = 'next' AND f.delay = 0
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
        WHERE f.forecast_id = i.forecast_id AND f.period = 'next' AND f.delay = 0
        OFFSET 0
    ) qn ON TRUE
    LEFT JOIN LATERAL (
        SELECT f.ave_change::float8 AS ave_5d,
               f.max_change::float8 AS max_5d,
               f.min_change::float8 AS min_5d
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id AND f.period = '5d' AND f.delay = 0
        OFFSET 0
    ) q5 ON TRUE
    LEFT JOIN LATERAL (
        SELECT f.ave_change::float8 AS ave_20d,
               f.max_change::float8 AS max_20d,
               f.min_change::float8 AS min_20d
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id AND f.period = '20d' AND f.delay = 0
        OFFSET 0
    ) q20 ON TRUE
    WHERE i.sec_type = $1
      AND i.bucket = 'mov_std'
      AND i.stat_month = $2
      AND m.ma_window >= $3::int
      AND m.k >= $4::numeric
    ORDER BY i.code, m.ma_window, m.k, m.side, u.trig_date
"""

# ---------------------------------------------------------------------------
#  mov_std indicator values (wide: one row per (code, date) with the
#  day's close — the value whose excess defines the band bar; band
#  inputs are never re-derived, trigger_excess carries them)
# ---------------------------------------------------------------------------

MOV_STD_VALUE_COLUMNS = ["code", "date", "value"]


def mov_std_values_sql(sec_type: str) -> str:
    """The wide close-value SELECT for the sec_type's own price
    convention (the parent mov_ave_spread sources: ETF uses the
    adjusted close when available). The close is aliased ``value`` —
    the engines' uniform indicator-value column."""
    base, price_expr = PRICE_SOURCE[sec_type]
    est_filter = AMT_SOURCE[sec_type][3]
    return f"""
    SELECT b.code,
           extract(epoch from b.date)::float8 AS date,
           {price_expr}::float8               AS value
    FROM {base}
    WHERE b.code = ANY($1::text[])
      AND b.date = ANY($2::date[])
      AND b.close IS NOT NULL
      {est_filter}
"""

__all__ = [
    "MOV_STD_BUCKET_COLUMNS",
    "MOV_STD_BUCKET_EPOCH_COLS",
    "MOV_STD_BUCKETS_SQL",
    "MOV_STD_VALUE_COLUMNS",
    "mov_std_values_sql",
]
