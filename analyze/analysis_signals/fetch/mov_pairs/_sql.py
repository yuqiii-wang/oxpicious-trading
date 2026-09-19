"""CASE-free SQL templates for the mov_pairs signal families
(mov_pairs / mov_pairs_ema).

Plain SELECTs only — no CASE/WHEN, no conditional expressions. The
bucket read fans the forecast bucket's trigger arrays out to a LONG
frame (one row per bucket trigger day) via LATERAL unnest, so every
column arrives as a plain scalar (arrays never cross into the frames);
the spread-value read is a WIDE literal-column SELECT over both fast
legs' spread columns of the parent mov_ave_spread detail tables (the
(fast_leg, window)→column mapping happens in the engine's vectorized
melt).

The two families share ONE template pair: the motivation table +
identities bucket + spread source are interpolation parameters (the
tables are structurally identical), and the emission slice arrives as
SQL predicates (window >= min, side = ANY).

Numeric columns are cast ``::float8`` at the SQL source and dates
leave as ``extract(epoch ...)::float8`` (the repo's asyncpg/cudf.pandas
contract — no Decimal objects, no object date columns downstream).
"""
from __future__ import annotations

from analyze.analysis_forecasts.config import (
    MOV_PAIRS_EMA_LEGS,
    MOV_PAIRS_EMA_WINDOWS,
    MOV_PAIRS_LEGS,
    MOV_PAIRS_WINDOWS,
    TABLE_FORECAST,
    TABLE_IDENTITIES,
    TABLE_MOV_PAIRS,
    TABLE_MOV_PAIRS_EMA,
)

# ---------------------------------------------------------------------------
#  Pair-cross buckets (long: one row per bucket × trigger day). One
#  template serves both families — only the motivation table +
#  identities bucket differ (identical column shapes).
# ---------------------------------------------------------------------------

MOV_PAIRS_BUCKET_COLUMNS = [
    "code", "stat_month", "fast_leg", "pair_window", "side",
    "is_market_hyped",
    "ave_change", "reverse_prob", "occurrence_count",
    "ave_next",
    "ave_5d", "max_5d", "min_5d",
    "ave_20d", "max_20d", "min_20d",
    "ave_60d", "max_60d", "min_60d",
    "trig_date", "trig_excess",
]
MOV_PAIRS_BUCKET_EPOCH_COLS = ("stat_month", "trig_date")


def mov_pairs_buckets_sql(bucket_table: str, bucket: str) -> str:
    """The long bucket-frame SELECT for one pair family —
    ``bucket_table`` the analysis_forecasts motivation table,
    ``bucket`` its forecast_identities bucket name. Emission slice as
    predicates: pair_window >= $3, side = ANY($4); BOTH fast legs
    emit."""
    return f"""
    SELECT i.code,
           extract(epoch from i.stat_month)::float8 AS stat_month,
           m.fast_leg,
           m.pair_window::int                       AS pair_window,
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
    JOIN {bucket_table} m
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
      AND i.bucket = '{bucket}'
      AND i.stat_month = $2
      AND m.pair_window >= $3::int
      AND m.side = ANY($4::text[])
    ORDER BY i.code, m.fast_leg, m.pair_window, m.side, u.trig_date
"""


# ---------------------------------------------------------------------------
#  Spread values (wide: one row per (code, date), one column per
#  (fast_leg, window) — the engine melts). One builder serves both
#  families: (source table, legs) parameters. The stored spread column
#  IS {fast_leg}_vs_ma{W} / {fast_leg}_vs_ema{W} (ma5_vs_ma60 /
#  price_vs_ma60 / ema6_vs_ema60 / price_vs_ema60 / ...); the output
#  column is the family's {prefix}_{W} melt key.
# ---------------------------------------------------------------------------

def mov_pairs_values_sql(
    table: str, legs: tuple[tuple[str, str], ...], windows: tuple[int, ...],
    suffix: str,
) -> tuple[str, list[str]]:
    """(SQL, output columns) of the wide spread-value SELECT for one
    pair family — ``table`` the parent mov_ave_spread detail table,
    ``legs`` the (fast_leg, prefix) pairs, ``suffix`` "ma" | "ema" (the
    slow-leg column stem)."""
    cols = ",\n       ".join(
        f"s.{leg}_vs_{suffix}{w}::float8 AS {prefix}_{w}"
        for leg, prefix in legs
        for w in windows
    )
    sql = f"""
    SELECT s.code,
           extract(epoch from s.date)::float8 AS date,
           {cols}
    FROM {table} s
    WHERE s.sec_type = $1
      AND s.code = ANY($2::text[])
      AND s.date = ANY($3::date[])
"""
    return sql, ["code", "date"] + [
        f"{prefix}_{w}"
        for _, prefix in legs for w in windows
    ]


# The two families' (motivation table, spread source table, fast legs,
# slow-leg windows, spread-column suffix) shapes — the engines'
# identity attrs read their own entry.
MOV_PAIRS_SPREAD_TABLE = "analysis.mov_ave_spreads_detail"
MOV_PAIRS_EMA_SPREAD_TABLE = "analysis.mov_ave_spreads_detail_ema"

PAIRS_FAMILY_SOURCES: dict[str, dict] = {
    "mov_pairs": {
        "bucket_table": TABLE_MOV_PAIRS,
        "spread_table": MOV_PAIRS_SPREAD_TABLE,
        "legs": MOV_PAIRS_LEGS,
        "windows": MOV_PAIRS_WINDOWS,
        "suffix": "ma",
    },
    "mov_pairs_ema": {
        "bucket_table": TABLE_MOV_PAIRS_EMA,
        "spread_table": MOV_PAIRS_EMA_SPREAD_TABLE,
        "legs": MOV_PAIRS_EMA_LEGS,
        "windows": MOV_PAIRS_EMA_WINDOWS,
        "suffix": "ema",
    },
}

__all__ = [
    "MOV_PAIRS_BUCKET_COLUMNS",
    "MOV_PAIRS_BUCKET_EPOCH_COLS",
    "mov_pairs_buckets_sql",
    "mov_pairs_values_sql",
    "PAIRS_FAMILY_SOURCES",
]
