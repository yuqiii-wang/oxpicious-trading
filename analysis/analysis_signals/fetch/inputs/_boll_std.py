"""The bollinger (boll_std) family's value source (analysis.
analysis_signals.fetch.inputs._boll_std — IN-ENGINE since 2026-10-10,
the analysis.mov_ave_boll table is gone): the fetch returns price +
boost axes over each code's FULL history; the family's _compute adds
the σ-depth z + episode columns on the frame.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.labels import SEC_TYPE_CODE
from _common.build_commons import rec_cols

from analysis.analysis_signals.config import BOLL_WINDOWS
from analysis.mov_ave_spread.config import OHL_EST_FILTER, PRICE_SOURCE

from ._shared import (
    PATTERN_TABLE,
    PX_VOL_TABLE,
    _FIRST_DATE_SOURCE,
    amt_ratio_fragments,
    finalize_input_frame,
)

# the bollinger family's PRE-compute fetch vocabulary (price + the boost
# axes — the z/episode columns are added in-engine by the family's
# _compute.add_boll_value_columns; the names here are the POST-compute
# frame's shape for the typed empty path)
_BOLL_FETCH_COLUMNS = ["code", "date", "price", "pattern", "vol_state",
                       "px_speed", "amt_climax"]
_BOLL_INPUT_COLUMNS = ["code", "date", "price"] + [
    f"boll_z_{w}days" for w in BOLL_WINDOWS
] + [f"is_typical_{w}days" for w in BOLL_WINDOWS] + [
    f"ep_size_{w}days" for w in BOLL_WINDOWS
] + [
    "pattern", "vol_state", "px_speed",
]


async def fetch_latest_boll_date(conn, sec_type: str) -> date | None:
    """The sec_type's LATEST AVAILABLE PRICE DATA DATE (max(date) on the
    base OHLCV table with the same close IS NOT NULL filter the input
    fetch uses) — the bollinger family's ROLLING LATEST snapshot key
    (the same data-keyed idempotence rule as the RSI leg: the family's
    values are computed from prices, so prices are its data horizon)."""
    return await conn.fetchval(
        f"SELECT max(b.date) FROM {_FIRST_DATE_SOURCE[sec_type]} b "
        f"WHERE b.close IS NOT NULL",
    )


async def fetch_boll_inputs(
    conn,
    sec_type: str,
    codes: list[str],
) -> pd.DataFrame:
    """The bollinger family's joined long-format input frame — the RSI
    fetch's base shape (one row per (code, date): the price in the
    mov_ave_spread convention — ETF adjusted close, index/stock basic
    close — plus the day-0 ohlcv-boost axis columns pattern / vol_state
    / px_speed, int8 codes, AXIS_MISSING where the axis store has no
    row) WITHOUT any value columns: the σ-depth z + episode layer are
    computed in-engine by the family's _compute.add_boll_value_columns.

    NO lower date bound — each code's FULL history is fetched, because
    the trailing SMA/σ windows need W prior rows for the earliest target
    date and the episode segmentation reads preceding band days
    arbitrarily far back (the RSI fetch can bound to ``since`` only
    because its values are precomputed over full history). No upper
    bound either (forward changes need post-snapshot prices). All
    numerics cast ::float8, the date epoch-converted — the frame the
    engine receives is cudf-representable from construction."""
    if not codes:
        return pd.DataFrame(columns=_BOLL_FETCH_COLUMNS)

    base, price_expr = PRICE_SOURCE[sec_type]
    st_code = SEC_TYPE_CODE[sec_type]
    ohl_est_filter = OHL_EST_FILTER[sec_type]
    amt_select, amt_joins = amt_ratio_fragments(sec_type)
    sql = f"""
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {price_expr}::float8 AS price,
               g.pattern,
               p.vol_state::float8 AS vol_state,
               p.px_speed::float8 AS px_speed,
               {amt_select}
        FROM {base}
        LEFT JOIN {PATTERN_TABLE} g
               ON g.sec_type = {st_code}
              AND g.code = b.code AND g.date = b.date
        LEFT JOIN {PX_VOL_TABLE} p
               ON p.sec_type = {st_code}
              AND p.code = b.code AND p.date = b.date
        {amt_joins}
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
          {ohl_est_filter}
        ORDER BY b.code, b.date ASC
    """
    rows = await conn.fetch(sql, sorted(codes))
    if not rows:
        return pd.DataFrame(columns=_BOLL_FETCH_COLUMNS)
    df = pd.DataFrame(
        rec_cols(rows), columns=_BOLL_FETCH_COLUMNS + ["amt_ratio"],
    )
    return finalize_input_frame(df)
