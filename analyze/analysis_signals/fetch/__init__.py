"""DB fetchers for analyze.analysis_signals.

Everything here is DATA ACCESS — every read lands in a cudf.pandas
DataFrame (the repo's cudf contract: ``::float8`` numeric casts at the
SQL source, epoch float8 dates materialized to datetime64[us] in ONE
host pass via epoch_col_to_dt64, column-materialized construction so
no numeric path ever sees object dtype). The engines consume the
frames; no dict-row processing anywhere.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64

from .mov_pairs import (
    MOV_PAIRS_BUCKET_COLUMNS,
    MOV_PAIRS_BUCKET_EPOCH_COLS,
    mov_pairs_buckets_sql,
    mov_pairs_values_sql,
    PAIRS_FAMILY_SOURCES,
)
from .mov_rsi import (
    MOV_RSI_BUCKET_COLUMNS,
    MOV_RSI_BUCKET_EPOCH_COLS,
    MOV_RSI_BUCKETS_SQL,
    MOV_RSI_VALUE_COLUMNS,
    MOV_RSI_VALUES_SQL,
)
from .mov_std import (
    MOV_STD_BUCKET_COLUMNS,
    MOV_STD_BUCKET_EPOCH_COLS,
    MOV_STD_BUCKETS_SQL,
    MOV_STD_VALUE_COLUMNS,
    mov_std_values_sql,
)


def _frame(
    rows: list,
    columns: list[str],
    epoch_cols: tuple[str, ...],
) -> pd.DataFrame:
    """Column-materialized frame from asyncpg records + the epoch→
    datetime64 materialization of the epoch columns (empty frame with
    the exact columns when the read came back empty)."""
    if not rows:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(rec_cols(rows), columns=columns)
    for col in epoch_cols:
        df[col] = epoch_col_to_dt64(df[col], index=df.index)
    return df


# ---------------------------------------------------------------------------
#  Buckets (long: one row per bucket × trigger day)
# ---------------------------------------------------------------------------

def _code_scoped(bucket_sql: str, codes: list[str] | None) -> str:
    """The buckets SELECT with the partial-backfill code filter injected
    before its ORDER BY ($5 — every family's buckets template stops at
    $4). Unscoped reads (codes None/empty) return the template as-is,
    keeping the hot path's exact SQL and argument count."""
    if not codes:
        return bucket_sql
    marker = "    ORDER BY i.code"
    return bucket_sql.replace(
        marker, f"      AND i.code = ANY($5::text[])\n{marker}", 1,
    )


async def fetch_mov_rsi_buckets(
    conn, sec_type: str, snapshot: date, pcts: tuple[int, ...],
    population: str | None = None,
    codes: list[str] | None = None,
) -> pd.DataFrame:
    """The sec_type's mov_rsi bucket rows for the snapshot at the
    given emission percentiles (long frame; buckets without triggers
    keep their NULL trigger columns). ``population`` scopes the read to
    ONE derived-population cohort (forecast_identities.ohlcv_type —
    None the plain parent, 'ohlcv_boost' / 'ohlcv_selection' the
    derived children): each cohort runs the complete emission as its
    own strategy set. ``codes`` scopes the read to the partial
    backfill's missing codes (None = every code)."""
    if codes:
        rows = await conn.fetch(
            _code_scoped(MOV_RSI_BUCKETS_SQL, codes),
            sec_type, snapshot, list(pcts), population, sorted(codes),
        )
    else:
        rows = await conn.fetch(
            MOV_RSI_BUCKETS_SQL, sec_type, snapshot, list(pcts), population,
        )
    return _frame(rows, MOV_RSI_BUCKET_COLUMNS, MOV_RSI_BUCKET_EPOCH_COLS)


async def fetch_mov_std_buckets(
    conn, sec_type: str, snapshot: date,
    ma_window_min: int, k_min: float,
    codes: list[str] | None = None,
) -> pd.DataFrame:
    """The sec_type's mov_std bucket rows for the snapshot at
    the emission slice (MA/σ windows >= ``ma_window_min``, σ multiples
    >= ``k_min``; long frame). ``codes`` scopes the read to the
    partial backfill's missing codes (None = every code)."""
    if codes:
        rows = await conn.fetch(
            _code_scoped(MOV_STD_BUCKETS_SQL, codes),
            sec_type, snapshot, ma_window_min, k_min, sorted(codes),
        )
    else:
        rows = await conn.fetch(
            MOV_STD_BUCKETS_SQL, sec_type, snapshot, ma_window_min, k_min,
        )
    return _frame(rows, MOV_STD_BUCKET_COLUMNS, MOV_STD_BUCKET_EPOCH_COLS)


async def fetch_mov_pairs_buckets(
    conn, sec_type: str, snapshot: date, signal_type: str,
    window_min: int, sides: tuple[str, ...],
    codes: list[str] | None = None,
) -> pd.DataFrame:
    """The sec_type's pair-cross bucket rows (whichever of the four
    families ``signal_type`` names) for the snapshot at the
    emission slice (slow-leg windows >= ``window_min``, sides in
    ``sides``; long frame). ``codes`` scopes the read to the partial
    backfill's missing codes (None = every code)."""
    source = PAIRS_FAMILY_SOURCES[signal_type]
    sql = _code_scoped(
        mov_pairs_buckets_sql(source["bucket_table"], signal_type), codes,
    )
    if codes:
        rows = await conn.fetch(
            sql, sec_type, snapshot, window_min, list(sides), sorted(codes),
        )
    else:
        rows = await conn.fetch(
            sql, sec_type, snapshot, window_min, list(sides),
        )
    return _frame(rows, MOV_PAIRS_BUCKET_COLUMNS, MOV_PAIRS_BUCKET_EPOCH_COLS)


# ---------------------------------------------------------------------------
#  Indicator values (wide, at the exact (code, date) points the
#  engines' triggers name — bounded reads, never whole windows)
# ---------------------------------------------------------------------------

async def fetch_mov_rsi_values(
    conn, sec_type: str, codes: list[str], dates: list[date],
) -> pd.DataFrame:
    """RSI values for every window column at the given (code, date)
    points (wide frame; NULL columns absent)."""
    if not codes or not dates:
        return pd.DataFrame(columns=MOV_RSI_VALUE_COLUMNS)
    rows = await conn.fetch(
        MOV_RSI_VALUES_SQL, sec_type, sorted(codes), dates,
    )
    return _frame(rows, MOV_RSI_VALUE_COLUMNS, ("date",))


async def fetch_mov_std_values(
    conn, sec_type: str, codes: list[str], dates: list[date],
) -> pd.DataFrame:
    """The day closes at the given (code, date) points (wide frame)."""
    if not codes or not dates:
        return pd.DataFrame(columns=MOV_STD_VALUE_COLUMNS)
    rows = await conn.fetch(
        mov_std_values_sql(sec_type), sorted(codes), dates,
    )
    return _frame(rows, MOV_STD_VALUE_COLUMNS, ("date",))


async def fetch_mov_pairs_values(
    conn, sec_type: str, codes: list[str], dates: list[date],
    signal_type: str,
) -> pd.DataFrame:
    """The family's stored relative spreads — BOTH fast legs (the
    ma5_vs_ma{W} / price_vs_ma{W} / ema6_vs_ema{W} / price_vs_ema{W}
    columns of whichever of the two families ``signal_type`` names) —
    PLUS the absolute legs (the fast-leg level ma5 / ema6 / the day's
    close and the slow-leg level ma{W} / ema{W}) at the given (code,
    date) points (wide frame, one ``{prefix}_{W}`` spread column per
    fast leg × slow-leg window)."""
    source = PAIRS_FAMILY_SOURCES[signal_type]
    sql, value_columns = mov_pairs_values_sql(
        source["spread_table"], source["legs"], source["windows"],
        source["suffix"], sec_type,
    )
    if not codes or not dates:
        return pd.DataFrame(columns=value_columns)
    rows = await conn.fetch(sql, sec_type, sorted(codes), dates)
    return _frame(rows, value_columns, ("date",))


__all__ = [
    "fetch_mov_rsi_buckets",
    "fetch_mov_std_buckets",
    "fetch_mov_pairs_buckets",
    "fetch_mov_rsi_values",
    "fetch_mov_std_values",
    "fetch_mov_pairs_values",
]
