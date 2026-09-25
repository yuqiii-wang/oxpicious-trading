"""CASE-free SQL templates for the mov_pairs signal families
(mov_pairs / mov_pairs_ema).

Plain SELECTs only — no CASE/WHEN, no conditional expressions. The
bucket read fans the forecast bucket's mixed-row DELAY LADDER + the
matching per-rung trigger arrays out to a LONG frame (one row per
bucket × ladder rung × trigger day) via LATERAL unnest, so every
column arrives as a plain scalar (arrays never cross into the frames);
the value read is a WIDE literal-column SELECT over both fast legs'
spread columns of the parent mov_ave_spread detail tables PLUS the
absolute legs (the fast-leg level ma5 / ema6 / close and the slow-leg
level ma{W} / ema{W} from stats.{sec_type}_tech_stats + the sec_type's
PRICE_SOURCE close) — the (fast_leg, window)→column mapping happens in
the engine's vectorized melt.

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
from analyze.analysis_forecasts.fetch._sources import PRICE_SOURCE
from analyze.analysis_signals.config import SIGNAL_DELAY_MAX

# ---------------------------------------------------------------------------
#  Pair-cross buckets (long: one row per bucket × ladder rung × trigger
#  day — the pairs families' runs are single days, so only rung 0
#  exists; the ladder shape is uniform with the streak families). One
#  template serves both families — only the motivation table +
#  identities bucket differ (identical column shapes).
# ---------------------------------------------------------------------------

MOV_PAIRS_BUCKET_COLUMNS = [
    "code", "stat_date", "fast_leg", "pair_window", "side",
    "regime_state",
    "delay",
    "ave_change", "occurrence_count",
    "ave_next",
    "ave_5d", "max_5d", "min_5d",
    "ave_20d", "max_20d", "min_20d",
    "trig_date", "trig_excess",
]
MOV_PAIRS_BUCKET_EPOCH_COLS = ("stat_date", "trig_date")


def mov_pairs_buckets_sql(bucket_table: str, bucket: str) -> str:
    """The long bucket-frame SELECT for one pair family —
    ``bucket_table`` the analysis_forecasts motivation table,
    ``bucket`` its forecast_identities bucket name. Emission slice as
    predicates: pair_window >= $3, side = ANY($4); BOTH fast legs
    emit."""
    return f"""
    SELECT i.code,
           extract(epoch from i.stat_date)::float8 AS stat_date,
           m.fast_leg,
           m.pair_window::int                       AS pair_window,
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
    JOIN {bucket_table} m
      ON m.forecast_id = i.forecast_id
    CROSS JOIN LATERAL (
        -- the bucket's mixed DELAY LADDER (one row per rung
        -- 0..{SIGNAL_DELAY_MAX}) — uniform with the streak families;
        -- the pairs' one-day signals only ever occupy rung 0
        SELECT f.delay, f.ave_change, f.occurrence_count
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id AND f.period = 'mixed'
          AND f.delay BETWEEN 0 AND {SIGNAL_DELAY_MAX}
        OFFSET 0
    ) fmx
    LEFT JOIN LATERAL (
        -- the rung's own trigger anchors (rung 0's cross days here)
        SELECT f.delay, f.trigger_dates, f.trigger_excess
        FROM {TABLE_FORECAST} f
        WHERE f.forecast_id = i.forecast_id AND f.period = 'next'
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
      AND i.bucket = '{bucket}'
      AND i.stat_date = $2
      AND m.pair_window >= $3::int
      AND m.side = ANY($4::text[])
    ORDER BY i.code, m.fast_leg, m.pair_window, m.side, fmx.delay,
             u.trig_date
"""


# ---------------------------------------------------------------------------
#  Spread + leg values (wide: one row per (code, date), one column per
#  (fast_leg, window) spread PLUS the absolute legs — the engine melts).
#  One builder serves both families: (source table, legs) parameters.
#  The stored spread column IS {fast_leg}_vs_ma{W} / {fast_leg}_vs_ema{W}
#  (ma5_vs_ma60 / price_vs_ma60 / ema6_vs_ema60 / price_vs_ema60 / ...);
#  the output column is the family's {prefix}_{W} melt key. The absolute
#  legs ride alongside — the fast-leg level (ma5 / ema6 from tech_stats,
#  the day's close from the sec_type's PRICE_SOURCE convention, the same
#  adjusted basis the spreads + tech_stats MAs are built on) and the
#  slow-leg level ma{W} / ema{W} — the strategy's signal_threshold and
#  the history rows' signal / signal_threshold (the live tier's "cross"
#  value space: fast leg vs the day's slow leg, never the zero line).
# ---------------------------------------------------------------------------

def mov_pairs_values_sql(
    table: str, legs: tuple[tuple[str, str], ...], windows: tuple[int, ...],
    suffix: str, sec_type: str,
) -> tuple[str, list[str]]:
    """(SQL, output columns) of the wide spread + leg-value SELECT for
    one pair family — ``table`` the parent mov_ave_spread detail table,
    ``legs`` the (fast_leg, prefix) pairs, ``suffix`` "ma" | "ema" (the
    slow-leg column stem), ``sec_type`` for the tech-stats table and
    the price convention."""
    cols = ",\n       ".join(
        f"s.{leg}_vs_{suffix}{w}::float8 AS {prefix}_{w}"
        for leg, prefix in legs
        for w in windows
    )
    fast_cols = sorted({leg for leg, _ in legs} - {"price"})
    fast_sel = ",\n       ".join(f"t.{c}::float8 AS {c}" for c in fast_cols)
    slow_sel = ",\n       ".join(
        f"t.{suffix}{w}::float8 AS {suffix}{w}" for w in windows
    )
    base, price_expr = PRICE_SOURCE[sec_type]
    # The base is a FROM item in its own right elsewhere; as a LEFT-JOIN
    # operand it needs parens ONLY when it embeds its own JOIN (the ETF
    # adjustment chain) — `(single_table alias)` is a Postgres syntax
    # error, a parenthesized join tree is not.
    base_join = f"({base})" if " JOIN " in base else base
    sql = f"""
    SELECT s.code,
           extract(epoch from s.date)::float8 AS date,
           {cols},
           {price_expr}::float8               AS close,
           {fast_sel},
           {slow_sel}
    FROM {table} s
    LEFT JOIN stats.{sec_type}_tech_stats t
      ON t.code = s.code AND t.date = s.date
    LEFT JOIN {base_join}
      ON b.code = s.code AND b.date = s.date
    WHERE s.sec_type = $1
      AND s.code = ANY($2::text[])
      AND s.date = ANY($3::date[])
"""
    return sql, (
        ["code", "date"]
        + [f"{prefix}_{w}" for _, prefix in legs for w in windows]
        + ["close", *fast_cols]
        + [f"{suffix}{w}" for w in windows]
    )


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
