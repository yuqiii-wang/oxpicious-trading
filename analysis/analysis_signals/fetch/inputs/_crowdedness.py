"""The crowdedness family's value source (analysis.analysis_signals.
fetch.inputs._crowdedness, 2026-11-16): the nightly market crowdedness
composite of ``analysis.market_crowdedness`` LEFT JOINed per (code,
sec_type, date) onto the base-OHLCV frame — the pool-level crowding
gauge measured over each index's member-stock pool (hot-set
concentration / flow / co-movement / valuation percentiles, blended
0.30/0.30/0.25/0.15 into ``crowdedness_score``), plus its 5-pool-date
moving average ``crowdedness_score_ma5`` (the pipeline's ONLINE read —
the raw score's day-over-day movement is single-day pillar noise).

The family trades the INDEX ITSELF (user-directed: the crowdedness
stats are the ASSISTANT metric; buy low / sell high on the index):
the fetch's ``price`` (the base close) stays the response axis — the
cells measure the index's own forward blend, the crowdedness columns
only carry the trigger.

Rows without a crowdedness row (a day the pool's gates left undefined,
or a base row before 2020-04) carry NULL scores — no trigger can fire
there (the engine's masks fill before compare), and the per-code
adaptive bars skip NULLs against min_periods (the rolling-quantile
contract). The family's universe is DATA-DRIVEN (the options-families
precedent): the codes with crowdedness rows at the sec_type — index
(413 codes) and, when the pipeline ever grows a signals sec_type for
it, industry; etf/stock resolve empty and the family skips.
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

# the crowdedness family's value sources: the nightly pool-level
# crowding gauges over BOTH hot-set bases — the DAILY-ROLLING hot set
# (re-ranked every date) and the THIS-YEAR pinned hot set (the date's
# own calendar year's frozen roster, joined on hot_year = year(date)) —
# plus the hot-set TECH profile (the tech_washout / tech_euphoria
# triggers read its median percentiles)
_CROWD_TABLE = "analysis.market_crowdedness"
_CROWD_YEAR_TABLE = "analysis.market_crowdedness_year"
_HOT_TECH_TABLE = "analysis.market_crowdedness_hot_tech"

# the crowdedness family's FETCHED vocabulary (the ctor shape)
_CROWD_FETCH_COLUMNS = ["code", "date", "price", "crowdedness_score",
                        "crowdedness_score_ma5", "crowdedness_score_year",
                        "crowdedness_score_ma5_year",
                        "median_px_vs_ma20_pct", "median_px_vs_ma60_pct",
                        "median_amt_vs_ma20_pct", "pattern", "vol_state",
                        "px_speed"]


async def resolve_crowdedness_universe(conn, sec_type: str) -> list[str]:
    """The crowdedness family's universe: distinct codes with crowdedness
    rows at the sec_type (``--codes`` is IGNORED for the family, the
    options-families/data-driven-universe precedent — a code without
    crowdedness data can never trigger). Empty for sec_types the
    crowdedness build does not cover (etf/stock) — the family skips."""
    st_code = SEC_TYPE_CODE.get(sec_type)
    if st_code is None:
        return []
    # analysis/analysis_signals/fetch/inputs/_crowdedness/crowdedness_universe
    view = render_sql_view(
        "analysis/analysis_signals/fetch/inputs/_crowdedness/"
        "crowdedness_universe",
        sec_type_code=frag(str(st_code)),
    )
    rows = await conn.fetch(view.sql, *view.params)
    return sorted(r["code"] for r in rows)


async def fetch_latest_crowdedness_date(conn, sec_type: str) -> date | None:
    """The sec_type's LATEST CROWDEDNESS DATA DATE (max(date) on
    analysis.market_crowdedness — the family's value source) — the
    family's ROLLING LATEST snapshot key (the data-keyed idempotence
    rule: the values come from that table, so its horizon is the
    key)."""
    return await conn.fetchval(
        f"SELECT max(date) FROM {_CROWD_TABLE} "
        f"WHERE sec_type = {SEC_TYPE_CODE[sec_type]}",
    )


async def fetch_crowdedness_inputs(conn, sec_type: str,
                                   codes: list[str]) -> pd.DataFrame:
    """The crowdedness family's joined long-format input frame — the base
    OHLCV frame with BOTH crowdedness hot-set bases LEFT JOINed per
    (code, date): the DAILY-ROLLING hot set (analysis.market_
    crowdedness.crowdedness_score / crowdedness_score_ma5) and the
    THIS-YEAR pinned hot set (analysis.market_crowdedness_year joined
    on hot_year = the date's own calendar year, the _year-suffixed
    columns — NULL where the pool's gates left the day undefined or
    the base row predates the crowdedness build): one row per
    (code, date), the price in the mov_ave_spread convention (the
    response axis — the family trades the index, the crowdedness
    columns carry the trigger), the day-0 ohlcv-boost axis columns
    (pattern / vol_state / px_speed, int8 codes, AXIS_MISSING where
    the axis store has no row).

    NO date bounds — each code's FULL history is fetched (the per-code
    adaptive trailing-quantile bars need every row; forward changes
    need post-trigger prices). All numerics cast ::float8, the date
    epoch-converted — the frame the engine receives is
    cudf-representable from construction."""
    if not codes:
        return pd.DataFrame(columns=_CROWD_FETCH_COLUMNS)

    base, price_expr = PRICE_SOURCE[sec_type]
    st_code = SEC_TYPE_CODE[sec_type]
    ohl_est_filter = OHL_EST_FILTER[sec_type]
    amt_select, amt_joins = amt_ratio_fragments(sec_type)
    # analysis/analysis_signals/fetch/inputs/_crowdedness/crowdedness_inputs
    view = render_sql_view(
        "analysis/analysis_signals/fetch/inputs/_crowdedness/"
        "crowdedness_inputs",
        codes=sorted(codes),
        price_expr=frag(price_expr),
        base_table=frag(base),
        crowd_table=frag(_CROWD_TABLE),
        crowd_year_table=frag(_CROWD_YEAR_TABLE),
        hot_tech_table=frag(_HOT_TECH_TABLE),
        sec_type_code=frag(str(st_code)),
        pattern_table=frag(PATTERN_TABLE),
        px_vol_table=frag(PX_VOL_TABLE),
        amt_select=frag(amt_select),
        amt_joins=frag(amt_joins),
        ohl_est_filter=frag(ohl_est_filter),
    )
    rows = await conn.fetch(view.sql, *view.params)
    if not rows:
        return pd.DataFrame(columns=_CROWD_FETCH_COLUMNS)
    df = pd.DataFrame(
        rec_cols(rows), columns=_CROWD_FETCH_COLUMNS + ["amt_ratio"],
    )
    return finalize_input_frame(df)
