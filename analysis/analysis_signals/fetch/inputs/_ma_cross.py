"""The ma_cross family's value source (analysis.analysis_signals.
fetch.inputs._ma_cross) — the EXISTING mov_ave gap table (analysis.
mov_ave_spreads_detail; NO new analysis table, NO in-engine SMA — the
table's price_vs_ma{W} columns ARE the values) with its EMA twin
(mov_ave_spreads_detail_ema) LEFT JOINed per (code, date).
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.labels import SEC_TYPE_CODE
from _common.build_commons import rec_cols

from analysis.mov_ave_spread.config import PRICE_SOURCE

from ._shared import (
    PATTERN_TABLE,
    PX_VOL_TABLE,
    amt_ratio_fragments,
    fetch_market_breadth,
    finalize_input_frame,
)

MA_GAP_TABLE = "analysis.mov_ave_spreads_detail"
_MA_GAP_WINDOWS = (20, 60, 120, 255)

_MA_EMA_TABLE = "analysis.mov_ave_spreads_detail_ema"
_MA_EMA_WINDOWS = (20, 60, 120, 255)
_MA_FETCH_COLUMNS = ["code", "date", "price", "price_slope"] + [
    f"gap{w}" for w in _MA_GAP_WINDOWS
] + [f"egap{w}" for w in _MA_EMA_WINDOWS]     + [
    "pattern", "vol_state", "px_speed", "amt_climax",
    # the market-level breadth state columns (MaBreadthEntry's values;
    # identical per date for every code — joined AFTER finalize)
    "mkt_breadth_ema10", "mkt_thrust", "mkt_9to1_up", "mkt_9to1_dn",
    "mkt_up_ratio", "mkt_dn_ratio",
]


async def fetch_ma_inputs(
    conn,
    sec_type: str,
    codes: list[str],
) -> pd.DataFrame:
    """The ma_cross family's joined long-format input frame: one row
    per (code, date) of the mov_ave_spreads_detail population — the
    table's own price_vs_ma{20,60,120,255} gaps (::float8, NULL during
    the parent pipeline's warmup) + its price_slope (the deep kind's
    up-day leg) — with the mov_ave_spread PRICE convention (ETF
    adjusted close) LEFT JOINed from the base OHLCV table for the
    in-engine forward changes, plus the day-0 ohlcv-boost axis columns
    (pattern / vol_state / px_speed as int8 codes).

    KEYED ON THE DETAIL TABLE (the family's value source owns the
    population — e.g. 931594's OHLC-estimated back-history carries
    table gaps but only part of it passes the base tables' clean-row
    filters; the gaps ride those closes legitimately, the same rows
    the parent analysis published). NO lower date bound — streaks and
    episode iso-gaps read each code's full history arbitrarily far
    back; no upper bound (forward changes need post-snapshot prices).
    Rows without a base price are dropped (no forward changes)."""
    if not codes:
        return pd.DataFrame(columns=_MA_FETCH_COLUMNS)

    base, price_expr = PRICE_SOURCE[sec_type]
    st_code = SEC_TYPE_CODE[sec_type]
    gap_cols = ",\n               ".join(
        f"d.price_vs_ma{w}::float8 AS gap{w}" for w in _MA_GAP_WINDOWS
    )
    amt_select, amt_joins = amt_ratio_fragments(
        sec_type, base_alias="d", ma_alias="d",
    )
    sql = f"""
        SELECT d.code,
               extract(epoch from d.date)::float8 AS date,
               {price_expr}::float8 AS price,
               d.price_slope::float8 AS price_slope,
               {gap_cols},
               e.price_vs_ema20::float8 AS egap20,
               e.price_vs_ema60::float8 AS egap60,
               e.price_vs_ema120::float8 AS egap120,
               e.price_vs_ema255::float8 AS egap255,
               g.pattern,
               p.vol_state::float8 AS vol_state,
               p.px_speed::float8 AS px_speed,
               {amt_select}
        FROM {MA_GAP_TABLE} d
        LEFT JOIN {_MA_EMA_TABLE} e
               ON e.sec_type = {st_code}
              AND e.code = d.code AND e.date = d.date
        LEFT JOIN {base}
               ON b.code = d.code AND b.date = d.date
        LEFT JOIN {PATTERN_TABLE} g
               ON g.sec_type = {st_code}
              AND g.code = d.code AND g.date = d.date
        LEFT JOIN {PX_VOL_TABLE} p
               ON p.sec_type = {st_code}
              AND p.code = d.code AND p.date = d.date
        {amt_joins}
        WHERE d.sec_type = {st_code}
          AND d.code = ANY($1::text[])
          AND {price_expr} IS NOT NULL
        ORDER BY d.code, d.date ASC
    """
    rows = await conn.fetch(sql, sorted(codes))
    if not rows:
        return pd.DataFrame(columns=_MA_FETCH_COLUMNS)
    df = pd.DataFrame(
        rec_cols(rows),
        columns=[c for c in _MA_FETCH_COLUMNS
                 if c not in ("mkt_breadth_ema10", "mkt_thrust",
                              "mkt_9to1_up", "mkt_9to1_dn",
                              "mkt_up_ratio", "mkt_dn_ratio")]
        + ["amt_ratio"],
    )
    df = finalize_input_frame(df)
    # the market-level breadth states join per DATE (identical context
    # for every code — MaBreadthEntry's value columns)
    breadth = await fetch_market_breadth(conn, sec_type)
    if not breadth.empty:
        df = df.merge(breadth, on="date", how="left")
    else:
        for col in ("mkt_breadth_ema10", "mkt_up_ratio", "mkt_dn_ratio"):
            df[col] = float("nan")
        for col in ("mkt_thrust", "mkt_9to1_up", "mkt_9to1_dn"):
            df[col] = False
    return df


async def fetch_latest_ma_date(conn, sec_type: str) -> date | None:
    """The sec_type's latest available mov_ave gap data date (max(date)
    on analysis.mov_ave_spreads_detail) — the ma_cross family's
    ROLLING LATEST snapshot key (the data-keyed idempotence rule: the
    family's values come from that table, so its horizon is the key)."""
    return await conn.fetchval(
        f"SELECT max(date) FROM {MA_GAP_TABLE} "
        f"WHERE sec_type = $1",
        SEC_TYPE_CODE[sec_type],
    )
