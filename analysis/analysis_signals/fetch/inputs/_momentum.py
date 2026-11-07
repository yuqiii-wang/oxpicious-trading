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
from _common.sql_views import frag, render_sql_view

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
    # analysis/analysis_signals/fetch/inputs/_momentum/momentum_inputs
    view = render_sql_view(
        "analysis/analysis_signals/fetch/inputs/_momentum/momentum_inputs",
        codes=sorted(codes),
        since=since,
        price_expr=frag(price_expr),
        base_table=frag(base),
        logret_cols=frag(logret_cols),
        momentum_table=frag(MOMENTUM_TABLE),
        sec_type_code=frag(str(st_code)),
        pattern_table=frag(PATTERN_TABLE),
        px_vol_table=frag(PX_VOL_TABLE),
        amt_select=frag(amt_select),
        amt_joins=frag(amt_joins),
        ohl_est_filter=frag(ohl_est_filter),
    )
    rows = await conn.fetch(view.sql, *view.params)
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
    # analysis/analysis_signals/fetch/inputs/_momentum/momentum_codes
    view = render_sql_view(
        "analysis/analysis_signals/fetch/inputs/_momentum/momentum_codes",
        momentum_table=frag(MOMENTUM_TABLE),
        sec_type=SEC_TYPE_CODE[sec_type],
    )
    rows = await conn.fetch(view.sql, *view.params)
    return {r["code"] for r in rows}
