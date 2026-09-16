"""opp_pair family inputs (analyze.analysis_forecasts.fetch.opp_pair).

Industry composite + offset-benchmark trend inputs of the industry
opposite-pair bucket family: the industry universe and pair set from
analysis_composites.industry_corr_benchmark_offsets, the industry
composite closes (stats.industry_basic_stats mean_close) and the
offset benchmark's closes. All frames follow the repo's cudf.pandas
load contract (::float8 numerics, epoch dates → datetime64[us]).
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64

from analyze.analysis_forecasts.config import (
    OPP_PAIR_BENCHMARK,
    OPP_PAIR_POOL_SIZE,
)

_INDUSTRY_CLOSE_COLUMNS = ["code", "date", "close"]


async def fetch_opp_pair_industries(conn) -> list[str]:
    """The opp_pair industry universe — every industry_id appearing in
    EITHER endpoint of the offsets-table pair set (pool / benchmark per
    the opp_pair config). Sorted."""
    rows = await conn.fetch(
        f"""
        SELECT ind FROM (
            SELECT industry_id AS ind
            FROM analysis_composites.industry_corr_benchmark_offsets
            WHERE pool_size = '{OPP_PAIR_POOL_SIZE}'
              AND benchmark_code = '{OPP_PAIR_BENCHMARK}'
            UNION
            SELECT benchmark_industry_id AS ind
            FROM analysis_composites.industry_corr_benchmark_offsets
            WHERE pool_size = '{OPP_PAIR_POOL_SIZE}'
              AND benchmark_code = '{OPP_PAIR_BENCHMARK}'
        ) s ORDER BY ind
        """
    )
    return [r["ind"] for r in rows]


async def fetch_opp_pair_pairs(conn) -> pd.DataFrame:
    """The opp_pair PAIR SET — one row per unordered pair (industry_id <
    pair_industry_id, the offsets table's storage order) with the pair's
    LATEST offsets-table context: the opposite score + offset_sub_corr of
    its most recent full 60d window (motivation context recorded on every
    bucket row; the latest score is a snapshot, not a look-ahead guard —
    the triggers/targets use only window-internal data)."""
    rows = await conn.fetch(
        f"""
        SELECT DISTINCT ON (industry_id, benchmark_industry_id)
               industry_id,
               benchmark_industry_id AS pair_industry_id,
               opposite_score_ma60_60d::float8 AS pair_score,
               offset_sub_corr_ma60_60d::float8 AS pair_corr,
               start_date AS score_date
        FROM analysis_composites.industry_corr_benchmark_offsets
        WHERE pool_size = '{OPP_PAIR_POOL_SIZE}'
          AND benchmark_code = '{OPP_PAIR_BENCHMARK}'
          AND opposite_score_ma60_60d IS NOT NULL
        ORDER BY industry_id, benchmark_industry_id, start_date DESC
        """
    )
    df = pd.DataFrame(
        rec_cols(rows),
        columns=[
            "industry_id", "pair_industry_id",
            "pair_score", "pair_corr", "score_date",
        ],
    )
    return df


async def fetch_industry_closes(
    conn, industries: list[str], since: date,
) -> pd.DataFrame:
    """Industry composite closes (mean_close, pool slice per the opp_pair
    config) as a long (code = industry_id, date, close) frame bounded to
    date >= ``since``, sorted by (code, date) — the build_grid input
    convention."""
    rows = await conn.fetch(
        f"""
        SELECT industry_id AS code,
               extract(epoch from date)::float8 AS date,
               mean_close::float8 AS close
        FROM stats.industry_basic_stats
        WHERE pool_size = '{OPP_PAIR_POOL_SIZE}'
          AND mean_close IS NOT NULL
          AND industry_id = ANY($1::text[])
          AND date >= $2
        ORDER BY industry_id, date ASC
        """,
        sorted(industries), since,
    )
    if not rows:
        return pd.DataFrame(columns=_INDUSTRY_CLOSE_COLUMNS)
    df = pd.DataFrame(rec_cols(rows), columns=_INDUSTRY_CLOSE_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    return df


async def fetch_industry_first_dates(
    conn, industries: list[str],
) -> dict[str, date]:
    """Per-industry TRUE first composite-close date (min(date) on the
    same rows fetch_industry_closes admits) — the full-window gate's
    first_ord input."""
    rows = await conn.fetch(
        f"""
        SELECT industry_id AS code, min(date) AS first_date
        FROM stats.industry_basic_stats
        WHERE pool_size = '{OPP_PAIR_POOL_SIZE}'
          AND mean_close IS NOT NULL
          AND industry_id = ANY($1::text[])
        GROUP BY industry_id
        """,
        sorted(industries),
    )
    return {r["code"]: r["first_date"] for r in rows}


async def fetch_benchmark_closes(
    conn, code: str, since: date,
) -> pd.Series:
    """The offset benchmark's close Series (date-indexed, ascending,
    bounded to date >= ``since``)."""
    rows = await conn.fetch(
        """
        SELECT extract(epoch from date)::float8 AS date,
               close::float8 AS close
        FROM stats.index_basic_stats
        WHERE code = $1 AND close IS NOT NULL AND date >= $2
        ORDER BY date ASC
        """,
        code, since,
    )
    if not rows:
        return pd.Series(dtype="float64")
    df = pd.DataFrame(rec_cols(rows), columns=["date", "close"])
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    return df.set_index("date")["close"].sort_index()
