"""The rebound (drop→rebound→resolution) family's value source
(analysis.analysis_signals.fetch.inputs._rebound, 2026-10-26): the
base-OHLCV price (the bollinger fetch's shape — the family computes its
own anchors/troughs/peaks in-engine from closes) PLUS the code's index
PE (stats.index_valuation.pe — the PE-conditioning axis
is_pe_boosted's input; LEFT JOINed 1:1 on (code, date), NULL where the
valuation store has no row — e.g. every non-index sec_type, whose
PE-gated cells then legitimately starve) and the day-0 ohlcv-boost
axes.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.labels import SEC_TYPE_CODE
from _common.build_commons import rec_cols
from _common.sql_views import frag, render_sql_view

from analysis.mov_ave_spread.config import OHL_EST_FILTER, PRICE_SOURCE

from ._shared import (
    PATTERN_TABLE,
    PX_VOL_TABLE,
    _FIRST_DATE_SOURCE,
    amt_ratio_fragments,
    finalize_input_frame,
)

# the rebound family's fetch vocabulary (price + PE + the boost axes —
# the anchors / trough / peak / resolution layer is computed in-engine)
_REBOUND_FETCH_COLUMNS = ["code", "date", "price", "pe", "pattern",
                          "vol_state", "px_speed", "amt_climax"]


async def fetch_latest_rebound_date(conn, sec_type: str) -> date | None:
    """The sec_type's LATEST AVAILABLE PRICE DATA DATE (max(date) on
    the base OHLCV table with the same close IS NOT NULL filter the
    input fetch uses) — the rebound family's ROLLING LATEST snapshot
    key (the bollinger convention: the family's values are computed
    from prices, so prices are its data horizon)."""
    return await conn.fetchval(
        f"SELECT max(b.date) FROM {_FIRST_DATE_SOURCE[sec_type]} b "
        f"WHERE b.close IS NOT NULL",
    )


async def fetch_rebound_inputs(conn, sec_type: str,
                               codes: list[str]) -> pd.DataFrame:
    """The rebound family's joined long-format input frame — the
    bollinger fetch's base shape plus the ``pe`` column: one row per
    (code, date), the price in the mov_ave_spread convention, the
    code's index PE (NULL where absent), and the day-0 ohlcv-boost
    axis columns (pattern / vol_state / px_speed, int8 codes,
    AXIS_MISSING where the axis store has no row).

    NO date bounds — each code's FULL history is fetched (the trailing
    anchor window needs W prior rows; the machine reads preceding days
    arbitrarily far back; forward changes need post-trigger prices).
    All numerics cast ::float8, the date epoch-converted — the frame
    the engine receives is cudf-representable from construction."""
    if not codes:
        return pd.DataFrame(columns=_REBOUND_FETCH_COLUMNS)

    base, price_expr = PRICE_SOURCE[sec_type]
    st_code = SEC_TYPE_CODE[sec_type]
    ohl_est_filter = OHL_EST_FILTER[sec_type]
    amt_select, amt_joins = amt_ratio_fragments(sec_type)
    # analysis/analysis_signals/fetch/inputs/_rebound/rebound_inputs
    view = render_sql_view(
        "analysis/analysis_signals/fetch/inputs/_rebound/rebound_inputs",
        codes=sorted(codes),
        price_expr=frag(price_expr),
        base_table=frag(base),
        sec_type_code=frag(str(st_code)),
        pattern_table=frag(PATTERN_TABLE),
        px_vol_table=frag(PX_VOL_TABLE),
        amt_select=frag(amt_select),
        amt_joins=frag(amt_joins),
        ohl_est_filter=frag(ohl_est_filter),
    )
    rows = await conn.fetch(view.sql, *view.params)
    if not rows:
        return pd.DataFrame(columns=_REBOUND_FETCH_COLUMNS)
    df = pd.DataFrame(
        rec_cols(rows), columns=_REBOUND_FETCH_COLUMNS + ["amt_ratio"],
    )
    return finalize_input_frame(df)
