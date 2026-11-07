"""The rf_break (roof/floor breakout) family's value source (analysis.
analysis_signals.fetch.inputs._rf_break, 2026-10-16) — the
DONCHIAN (method 1: the strictly-causal window's max intraday high /
min intraday low) and MEDIAN (method 5: the NaN-skipping median of the
five estimate methods per (date, period) — the cross-method CONSENSUS)
rows of analysis.mov_ave_spreads_roof_floor_estimates, pivoted to one
level column per (method, window, side): roof{W}/floor{W} donchian,
mroof{W}/mfloor{W} median, with the day-0 ohlcv-boost axes (pattern /
(vol_state, px_speed) / the turnover-climax amt axis) LEFT JOINed per
(code, date) and the code's index PE (stats.index_valuation.pe — the
shared PE-conditioning axis is_pe_boosted's input, 2026-10-26; NULL
where the valuation store has no row — e.g. every non-index sec_type,
whose PE-gated variants then legitimately starve).

The levels are STRUCTURAL bars (window extremes / method consensus,
not percentile tails): the engine's trigger is the close crossing the
PRIOR day's level (grouped shift — the trigger day's own level never
sets its own bar), so the fetch carries the levels UNshifted; the
breach pct columns are added in-engine. The price basis is the
estimates table's own today_close (the same closes the levels were
computed on — the COALESCE(adj_close, close) ETF convention).
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.labels import SEC_TYPE_CODE
from _common.build_commons import rec_cols
from _common.sql_views import frag, render_sql_view

from analysis.analysis_signals.config import RF_BREAK_METHODS, RF_BREAK_WINDOWS

from ._shared import (
    PATTERN_TABLE,
    PX_VOL_TABLE,
    RF_ESTIMATE_TABLE,
    amt_ratio_fragments,
    finalize_input_frame,
)

# the level column prefix per method: donchian roof{w}/floor{w},
# median mroof{w}/mfloor{w}
_METHOD_PREFIX = {"": "", "_med": "m"}


def _level_columns() -> list[str]:
    cols: list[str] = []
    for infix, _ in RF_BREAK_METHODS:
        p = _METHOD_PREFIX[infix]
        for w in RF_BREAK_WINDOWS:
            cols.extend((f"{p}roof{w}", f"{p}floor{w}"))
    return cols


_RF_INPUT_COLUMNS = ["code", "date", "price", "pe"] + _level_columns() + [
    "pattern", "vol_state", "px_speed", "amt_climax",
]


async def fetch_latest_rf_date(conn, sec_type: str) -> date | None:
    """The sec_type's latest breakout-method estimate date (max(date)
    on the estimates table over the family's methods) — the rf_break
    family's ROLLING LATEST snapshot key (the data-keyed idempotence
    rule: the family's bars come from that table, so its horizon is
    the key)."""
    method_codes = ",".join(str(m) for _, m in RF_BREAK_METHODS)
    return await conn.fetchval(
        f"SELECT max(date) FROM {RF_ESTIMATE_TABLE} "
        f"WHERE sec_type = $1 AND method IN ({method_codes})",
        SEC_TYPE_CODE[sec_type],
    )


async def fetch_rf_inputs(
    conn,
    sec_type: str,
    codes: list[str],
) -> pd.DataFrame:
    """The rf_break family's joined long-format input frame: one row
    per (code, date) with the estimates table's own today_close as the
    price plus one level column per (method, window, side) — the
    donchian and median rows MAX-aggregated per (code, date) off the
    long table (one row per (code, date, period, method)) — and the
    day-0 boost axes (pattern / vol_state / px_speed int8 codes + the
    bucketed turnover-climax amt axis).

    NO date bounds — the prior-day shift reads each code's full level
    history, and forward changes need post-trigger prices. Codes
    without a single estimate row yield no rows here (the caller
    intersects the universe with the estimates table's codes)."""
    if not codes:
        return pd.DataFrame(columns=_RF_INPUT_COLUMNS)

    st_code = SEC_TYPE_CODE[sec_type]
    level_cols = ",\n       ".join(
        f"max(e.roof_estimate) FILTER (WHERE e.period = {w} "
        f"AND e.method = {m})::float8 AS {p}roof{w},\n       "
        f"max(e.floor_estimate) FILTER (WHERE e.period = {w} "
        f"AND e.method = {m})::float8 AS {p}floor{w}"
        for infix, m in RF_BREAK_METHODS
        for w in RF_BREAK_WINDOWS
        for p in (_METHOD_PREFIX[infix],)
    )
    method_codes = ",".join(str(m) for _, m in RF_BREAK_METHODS)
    amt_select, amt_joins = amt_ratio_fragments(sec_type, base_alias="d")
    # analysis/analysis_signals/fetch/inputs/_rf_break/rf_inputs
    view = render_sql_view(
        "analysis/analysis_signals/fetch/inputs/_rf_break/rf_inputs",
        codes=sorted(codes),
        rf_estimate_table=frag(RF_ESTIMATE_TABLE),
        sec_type_code=frag(str(st_code)),
        method_codes=frag(method_codes),
        level_cols=frag(level_cols),
        outer_level_cols=frag(", d.".join(_level_columns())),
        pattern_table=frag(PATTERN_TABLE),
        px_vol_table=frag(PX_VOL_TABLE),
        amt_select=frag(amt_select),
        amt_joins=frag(amt_joins),
    )
    rows = await conn.fetch(view.sql, *view.params)
    if not rows:
        return pd.DataFrame(columns=_RF_INPUT_COLUMNS)
    # amt_ratio is bucketed + dropped by the shared finalize step
    columns = [c for c in _RF_INPUT_COLUMNS] + ["amt_ratio"]
    df = pd.DataFrame(rec_cols(rows), columns=columns)
    return finalize_input_frame(df)
