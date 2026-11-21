"""The volatility family's value source (analysis.analysis_signals.
fetch.inputs._volatility, 2026-11-18): the realized-volatility panel of
``analysis.volatility`` (the daily per-security vol build — hv across
six windows, the RiskMetrics EWMA vol, the OHLC estimators, ATR/NATR,
the percentile/z regime gauges) LEFT JOINed per (code, sec_type, date)
onto the base-OHLCV frame.

The family trades the SECURITY ITSELF (the crowdedness precedent: the
vol columns are the TRIGGER, the fetch's ``price`` — the base close in
the mov_ave_spread convention — stays the response axis; the cells
measure the code's own forward blend while the vol axes carry the
trigger). The fetched axes are the two the family's sub types ride:
``hv_20`` (the canonical 20-row close-to-close realized vol) and
``ewma_vol`` (the RiskMetrics fast vol, λ=0.94) — the adaptive tail
BARS compute in-engine per code (the mov_rsi convention; the panel's
own 252-row pct_rank/z gauges are panel-side readings, never the
family's bars).

Rows without a volatility row (a base row outside the panel's built
scope — the panel runs index only for now, the 10 study indices)
carry NULL vols — no trigger can fire there (the engine's masks fill
before compare), and the trailing quantile windows skip NULLs against
min_periods. The family's universe is DATA-DRIVEN (the crowdedness/
options-families precedent): the codes with volatility rows at the
sec_type — index (the panel's run scope) for now; etf/stock resolve
empty and the family skips until the panel's ENABLED_SEC_TYPES
widens (no signals-side change then).
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
    amt_ratio_fragments,
    finalize_input_frame,
)

# the volatility family's value source: the realized-vol panel
_VOL_TABLE = "analysis.volatility"

# the volatility family's FETCHED vocabulary (the ctor shape — the two
# axes the sub types ride; the adaptive tail bars compute in-engine)
_VOL_FETCH_COLUMNS = ["code", "date", "price", "hv_20", "ewma_vol",
                      "pattern", "vol_state", "px_speed"]


async def resolve_volatility_universe(conn, sec_type: str) -> list[str]:
    """The volatility family's universe: distinct codes with volatility
    rows at the sec_type (``--codes`` is IGNORED for the family, the
    crowdedness/data-driven-universe precedent — a code without vol
    rows can never trigger). Empty for sec_types the vol panel does
    not cover (etf/stock while the panel runs index only) — the family
    skips."""
    st_code = SEC_TYPE_CODE.get(sec_type)
    if st_code is None:
        return []
    # analysis/analysis_signals/fetch/inputs/_volatility/volatility_universe
    view = render_sql_view(
        "analysis/analysis_signals/fetch/inputs/_volatility/"
        "volatility_universe",
        sec_type_code=frag(str(st_code)),
    )
    rows = await conn.fetch(view.sql, *view.params)
    return sorted(r["code"] for r in rows)


async def fetch_latest_volatility_date(conn, sec_type: str) -> date | None:
    """The sec_type's LATEST VOLATILITY DATA DATE (max(date) on
    analysis.volatility — the family's value source) — the family's
    ROLLING LATEST snapshot key (the data-keyed idempotence rule: the
    values come from that table, so its horizon is the key)."""
    return await conn.fetchval(
        f"SELECT max(date) FROM {_VOL_TABLE} "
        f"WHERE sec_type = {SEC_TYPE_CODE[sec_type]}",
    )


async def fetch_volatility_inputs(conn, sec_type: str,
                                  codes: list[str]) -> pd.DataFrame:
    """The volatility family's joined long-format input frame — the base
    OHLCV frame with the realized-vol panel LEFT JOINed per (code,
    date): one row per (code, date), the price in the mov_ave_spread
    convention (the response axis — the family trades the security,
    the vol columns carry the trigger), the fetched vol axes (hv_20 /
    ewma_vol — NULL where the panel has no row), the day-0 ohlcv-boost
    axis columns (pattern / vol_state / px_speed, int8 codes,
    AXIS_MISSING where the axis store has no row).

    NO date bounds — each code's FULL history is fetched (the per-code
    adaptive trailing-quantile bars need every row; forward changes
    need post-trigger prices). All numerics cast ::float8, the date
    epoch-converted — the frame the engine receives is
    cudf-representable from construction."""
    if not codes:
        return pd.DataFrame(columns=_VOL_FETCH_COLUMNS)

    base, price_expr = PRICE_SOURCE[sec_type]
    st_code = SEC_TYPE_CODE[sec_type]
    ohl_est_filter = OHL_EST_FILTER[sec_type]
    amt_select, amt_joins = amt_ratio_fragments(sec_type)
    # analysis/analysis_signals/fetch/inputs/_volatility/volatility_inputs
    view = render_sql_view(
        "analysis/analysis_signals/fetch/inputs/_volatility/"
        "volatility_inputs",
        codes=sorted(codes),
        price_expr=frag(price_expr),
        base_table=frag(base),
        vol_table=frag(_VOL_TABLE),
        sec_type_code=frag(str(st_code)),
        pattern_table=frag(PATTERN_TABLE),
        px_vol_table=frag(PX_VOL_TABLE),
        amt_select=frag(amt_select),
        amt_joins=frag(amt_joins),
        ohl_est_filter=frag(ohl_est_filter),
    )
    rows = await conn.fetch(view.sql, *view.params)
    if not rows:
        return pd.DataFrame(columns=_VOL_FETCH_COLUMNS)
    df = pd.DataFrame(
        rec_cols(rows), columns=_VOL_FETCH_COLUMNS + ["amt_ratio"],
    )
    return finalize_input_frame(df)
