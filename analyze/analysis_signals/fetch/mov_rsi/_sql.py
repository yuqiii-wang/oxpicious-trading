"""CASE-free SQL templates for the mov_rsi signal family.

Plain SELECTs only — no CASE/WHEN, no conditional expressions. The
bucket read fans the forecast bucket's mixed-row DELAY LADDER + the
matching per-rung trigger arrays out to a LONG frame (one row per
bucket × ladder rung × trigger day) via LATERAL unnest, so every
column arrives as a plain scalar (arrays never cross into the frames);
the indicator-value read is a WIDE literal-column SELECT (the
window→column mapping happens in the engine's vectorized melt).

The read is POPULATION-PARAMETERIZED ($4 = the
forecast_identities.ohlcv_type verdict — NULL the plain parent,
'ohlcv_boost' / 'ohlcv_selection' the derived children): each
population runs the COMPLETE emission as its OWN cohort (the
2026-09-28 separate-cohort registration — boost the same trigger days
score-weighted, selection the favorable-anatomy subset, bottom side
only; each cohort's confidence is its own chosen-rung mean and ranks
separately in signal_order, per
temp_scripts/study_ohlcv_signal_confidence.py — folding the children's
means INTO the parent confidence was measured to degrade the OOS
rank).

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
from analyze.analysis_signals.config import SIGNAL_DELAY_MAX

# ---------------------------------------------------------------------------
#  mov_rsi buckets (long: one row per bucket × ladder rung × trigger day)
# ---------------------------------------------------------------------------

MOV_RSI_BUCKET_COLUMNS = [
    "code", "stat_date", "rsi_window", "pct", "side", "regime_state",
    "delay",
    "ave_change", "occurrence_count",
    "ave_next",
    "ave_5d", "max_5d", "min_5d",
    "ave_20d", "max_20d", "min_20d",
    "trig_date", "trig_excess",
]
MOV_RSI_BUCKET_EPOCH_COLS = ("stat_date", "trig_date")

MOV_RSI_BUCKETS_SQL = f"""
    SELECT i.code,
           extract(epoch from i.stat_date)::float8 AS stat_date,
           m.rsi_window::int                        AS rsi_window,
           m.pct::int                               AS pct,
           m.side,
           m.regime_state,
           fmx.delay::int                           AS delay,
           fmx.ave_change::float8                   AS ave_change,
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
    JOIN {TABLE_MOV_RSI} m
      ON m.forecast_id = i.forecast_id
    CROSS JOIN LATERAL (
        -- the POPULATION's own mixed DELAY LADDER (one row per rung
        -- 0..{SIGNAL_DELAY_MAX}: rung d's stats are conditioned on the
        -- signal having lasted d days) — the plain gate + the
        -- occurrence × edge balance rule run PER RUNG in the engine.
        -- $4 is the cohort's ohlcv_type verdict (NULL = the parent)
        SELECT f.delay, f.ave_change, f.occurrence_count
        FROM {TABLE_FORECAST} f
        -- the derived-population markers live on the identities registry
        -- (the 2026-09-26 identity migration)
        WHERE f.forecast_id = i.forecast_id
          AND i.ohlcv_type IS NOT DISTINCT FROM $4::text
          AND f.period = 'mixed'
          AND f.delay BETWEEN 0 AND {SIGNAL_DELAY_MAX}
        OFFSET 0
    ) fmx
    LEFT JOIN LATERAL (
        -- the rung's own trigger anchors (the day-d days of the
        -- bucket's qualifying streaks — a delayed strategy's history
        -- rows are its CHOSEN rung's anchors)
        SELECT f.delay, f.trigger_dates, f.trigger_excess
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id
          AND i.ohlcv_type IS NOT DISTINCT FROM $4::text
          AND f.period = 'next'
          AND f.delay BETWEEN 0 AND {SIGNAL_DELAY_MAX}
        OFFSET 0
    ) fnx ON fnx.delay = fmx.delay
    LEFT JOIN LATERAL unnest(fnx.trigger_dates, fnx.trigger_excess)
      AS u(trig_date, trig_excess) ON TRUE
    LEFT JOIN LATERAL (
        -- the quality periods' forward profiles (bucket-level
        -- columns for the final SignalQuality gate; 'next' has no
        -- close-based max/min — its max/min check is not applicable)
        SELECT f.ave_change::float8 AS ave_next
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id
          AND i.ohlcv_type IS NOT DISTINCT FROM $4::text
          AND f.period = 'next' AND f.delay = 0
        OFFSET 0
    ) qn ON TRUE
    LEFT JOIN LATERAL (
        SELECT f.ave_change::float8 AS ave_5d,
               f.max_change::float8 AS max_5d,
               f.min_change::float8 AS min_5d
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id
          AND i.ohlcv_type IS NOT DISTINCT FROM $4::text
          AND f.period = '5d' AND f.delay = 0
        OFFSET 0
    ) q5 ON TRUE
    LEFT JOIN LATERAL (
        SELECT f.ave_change::float8 AS ave_20d,
               f.max_change::float8 AS max_20d,
               f.min_change::float8 AS min_20d
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id
          AND i.ohlcv_type IS NOT DISTINCT FROM $4::text
          AND f.period = '20d' AND f.delay = 0
        OFFSET 0
    ) q20 ON TRUE
    WHERE i.sec_type = $1
      AND i.bucket = 'mov_rsi'
      AND i.stat_date = $2
      AND i.ohlcv_type IS NOT DISTINCT FROM $4::text
      AND m.pct = ANY($3::int[])
    ORDER BY i.code, m.rsi_window, m.pct, m.side, fmx.delay, u.trig_date
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
