"""The momentum (log-return) family's input fetchers (analysis.
analysis_signals.fetch.inputs._momentum) — the joined (code, date) frame
of price + the log-return columns over analysis.mov_ave_momentum (the
mov_ave_spread momentum step's store), the momentum-leg universe
pre-filter. The value axis is computed at the SQL boundary:
logret = ln(1.0 + momentum) — the log transform that makes the
per-code percentile bars self-adaptive (symmetric additive space,
comparable across codes and vol regimes).
"""
from __future__ import annotations

from datetime import date
from typing import Set

import pandas as pd

from _common.labels import SEC_TYPE_CODE
from _common.build_commons import rec_cols

from analysis.analysis_signals.config import MOMENTUM_AXES
from analysis.mov_ave_spread.config import OHL_EST_FILTER, PRICE_SOURCE

from ._shared import (
    MOMENTUM_TABLE,
    PATTERN_TABLE,
    PX_VOL_TABLE,
    amt_ratio_fragments,
    finalize_input_frame,
)

_INPUT_COLUMNS = ["code", "date", "price"] + [
    f"logret_{axis}" for axis in MOMENTUM_AXES
] + ["pattern", "vol_state", "px_speed", "amt_climax"]


async def fetch_momentum_inputs(
    conn,
    sec_type: str,
    codes: list[str],
    since: date,
) -> pd.DataFrame:
    """The joined long-format input frame for the given codes: one row
    per (code, date) with the price (the mov_ave_spread convention —
    ETF adjusted close) + one logret_{axis} column per momentum axis —
    ln(1.0 + momentum_{axis}) over analysis.mov_ave_momentum, NULL
    where the momentum store has no row (the base price history extends
    earlier than the momentum store's 2020 start).

    Rows are bounded to date >= ``since``; there is NO upper date bound
    — forward changes for the newest trigger days need post-trigger
    prices. Codes without a single momentum row are dropped by the
    code pre-filter."""
    if not codes:
        return pd.DataFrame(columns=_INPUT_COLUMNS)

    base, price_expr = PRICE_SOURCE[sec_type]
    st_code = SEC_TYPE_CODE[sec_type]
    ohl_est_filter = OHL_EST_FILTER[sec_type]
    logret_cols = ",\n       ".join(
        f"ln(1.0 + m.momentum_{axis}::float8) AS logret_{axis}"
        for axis in MOMENTUM_AXES
    )
    amt_select, amt_joins = amt_ratio_fragments(sec_type)
    sql = f"""
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {price_expr}::float8 AS price,
               {logret_cols},
               g.pattern,
               p.vol_state::float8 AS vol_state,
               p.px_speed::float8 AS px_speed,
               {amt_select}
        FROM {base}
        LEFT JOIN {MOMENTUM_TABLE} m
               ON m.sec_type = {st_code}
              AND m.code = b.code AND m.date = b.date
        LEFT JOIN {PATTERN_TABLE} g
               ON g.sec_type = {st_code}
              AND g.code = b.code AND g.date = b.date
        LEFT JOIN {PX_VOL_TABLE} p
               ON p.sec_type = {st_code}
              AND p.code = b.code AND p.date = b.date
        {amt_joins}
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
          AND b.date >= $2
          {ohl_est_filter}
        ORDER BY b.code, b.date ASC
    """
    rows = await conn.fetch(sql, sorted(codes), since)
    if not rows:
        return pd.DataFrame(columns=_INPUT_COLUMNS)
    # Column-materialized ctor; the shared finalize step does the
    # epoch->datetime64[us] pass + the boost-axis int8 code casts.
    df = pd.DataFrame(
        rec_cols(rows), columns=_INPUT_COLUMNS + ["amt_ratio"],
    )
    return finalize_input_frame(df)


async def fetch_momentum_codes(conn, sec_type: str) -> Set[str]:
    """Codes carrying at least one momentum row for the sec_type — the
    universe pre-filter's momentum leg (a code without momentum data
    can never enter a tail bucket)."""
    rows = await conn.fetch(
        f"SELECT DISTINCT code FROM {MOMENTUM_TABLE} WHERE sec_type = $1",
        SEC_TYPE_CODE[sec_type],
    )
    return {r["code"] for r in rows}
