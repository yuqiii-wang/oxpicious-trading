"""The mov_rsi (RSI percentile) family's input fetchers (analysis.
analysis_signals.fetch.inputs._mov_rsi) — the joined (code, date)
frame of price + the RSI window columns (analysis.mov_ave_rsi — the
value source), the RSI-leg universe pre-filter, the latest RSI data
date (the ROLLING LATEST snapshot key) and the curve tier's
per-index-class pooling lookup. The frame also carries the three
day-0 ohlcv-boost axis columns (_common/labels — the
ohlc_gaps pattern + the price_vs_amt (vol_state, px_speed) cell,
LEFT JOINed per (code, date), missing → the AXIS_MISSING int8 code).
"""
from __future__ import annotations

from datetime import date
from typing import Set

import pandas as pd

from _common.labels import SEC_TYPE_CODE
from _common.build_commons import rec_cols
from _common.sql_views import frag, render_sql_view

from analysis.analysis_signals.config import RSI_WINDOWS
from analysis.mov_ave_spread.config import OHL_EST_FILTER, PRICE_SOURCE

from ._shared import (
    PATTERN_TABLE,
    PX_VOL_TABLE,
    RSI_TABLE,
    amt_ratio_fragments,
    finalize_input_frame,
)

_INPUT_COLUMNS = ["code", "date", "price"] + [
    f"rsi_{w}days" for w in RSI_WINDOWS
] + [f"is_typical_{w}days" for w in RSI_WINDOWS] + [
    f"ep_size_{w}days" for w in RSI_WINDOWS
] + [
    "pattern", "vol_state", "px_speed", "amt_climax",
]


async def fetch_latest_rsi_date(conn, sec_type: str) -> date | None:
    """The sec_type's LATEST AVAILABLE RSI DATA DATE (max(date) on
    analysis.mov_ave_rsi) — the ROLLING LATEST snapshot's key and
    window end. Keying on DATA (not ``last_business_day()``) keeps the
    key idempotent across weekends / holidays (no data → no new key →
    the run is a clean no-op) and never ahead of the value source.
    Returns None when the table has no rows for the sec_type."""
    return await conn.fetchval(
        f"SELECT max(date) FROM {RSI_TABLE} WHERE sec_type = $1",
        SEC_TYPE_CODE[sec_type],
    )


async def fetch_rsi_inputs(
    conn,
    sec_type: str,
    codes: list[str],
    since: date,
) -> pd.DataFrame:
    """The joined long-format input frame for the given codes: one row
    per (code, date) with the price (the mov_ave_spread convention —
    ETF adjusted close) + one rsi_{W}days column per RSI window + the
    day-level tail-episode flags + episode sizes (the rsi analysis
    layer's population semantics) + the day-0 ohlcv-boost axis columns
    (pattern / vol_state / px_speed — int8 codes, AXIS_MISSING where
    the axis store has no row).

    Rows are bounded to date >= ``since`` (the earliest trailing-window
    start across the target snapshots); there is NO upper date bound —
    forward changes for the last bucket days of the newest snapshot
    need post-snapshot-end prices. Codes without a single RSI row are
    dropped by the code pre-filter (the caller intersects the universe
    with the RSI table's codes)."""
    if not codes:
        return pd.DataFrame(columns=_INPUT_COLUMNS)

    base, price_expr = PRICE_SOURCE[sec_type]
    st_code = SEC_TYPE_CODE[sec_type]
    ohl_est_filter = OHL_EST_FILTER[sec_type]
    rsi_cols = ",\n       ".join(
        f"r.rsi_{w}days::float8 AS rsi_{w}days" for w in RSI_WINDOWS
    )
    # the day-level tail-episode flags (TRUE = the first-touch pass's
    # days / FALSE = the re-entry pass's days / NULL out-of-band — the
    # gap-1 continuations inherit their pass's flag) + the EPISODE
    # SIZES (every in-band day — start, re-entry AND the gap-1
    # continuations — carries its episode's trading-day count; NULL
    # out-of-band). Owned by the consolidated trends component since
    # 2026-10-15: analysis.trend_episode_days (LEFT JOIN — a missing
    # row = out-of-band for every window; the retired is_typical
    # columns no longer ride analysis.mov_ave_rsi). BOTH signal tiers'
    # population rides the sizes (non-NULL = in-band — the deep days
    # the percentile bars select are the gap-1 continuations), the
    # curve tier weighting each day 1/size.
    flag_cols = ",\n       ".join(
        f"ted.is_typical_{w}days::int::float8 AS is_typical_{w}days"
        for w in RSI_WINDOWS
    )
    size_cols = ",\n       ".join(
        f"ted.typical_ep_size_{w}days::float8 AS ep_size_{w}days"
        for w in RSI_WINDOWS
    )
    amt_select, amt_joins = amt_ratio_fragments(sec_type)
    # analysis/analysis_signals/fetch/inputs/_mov_rsi/rsi_inputs — the
    # r CTE's sec_type filter is the "$3" positional reference (the
    # original text binds codes as $1 / since as $2 while the CTE stands
    # first), so it rides verbatim and st_code is bound last
    view = render_sql_view(
        "analysis/analysis_signals/fetch/inputs/_mov_rsi/rsi_inputs",
        codes=sorted(codes),
        since=since,
        r_sec_type=frag("$3"),
        rsi_table=frag(RSI_TABLE),
        price_expr=frag(price_expr),
        rsi_cols=frag(rsi_cols),
        flag_cols=frag(flag_cols),
        size_cols=frag(size_cols),
        amt_select=frag(amt_select),
        base_table=frag(base),
        sec_type_code=frag(str(st_code)),
        pattern_table=frag(PATTERN_TABLE),
        px_vol_table=frag(PX_VOL_TABLE),
        amt_joins=frag(amt_joins),
        ohl_est_filter=frag(ohl_est_filter),
    )
    rows = await conn.fetch(view.sql, *view.params, st_code)
    if not rows:
        return pd.DataFrame(columns=_INPUT_COLUMNS)
    # Column-materialized ctor; the shared finalize step does the
    # epoch→datetime64[us] pass + the boost-axis int8 code casts (+
    # the amt_ratio bucket).
    df = pd.DataFrame(
        rec_cols(rows), columns=_INPUT_COLUMNS + ["amt_ratio"],
    )
    return finalize_input_frame(df)


async def fetch_rsi_codes(conn, sec_type: str) -> Set[str]:
    """Codes carrying at least one RSI row for the sec_type — the
    universe pre-filter's RSI leg (a code without RSI data can never
    enter a percentile bucket)."""
    # analysis/analysis_signals/fetch/inputs/_mov_rsi/rsi_codes
    view = render_sql_view(
        "analysis/analysis_signals/fetch/inputs/_mov_rsi/rsi_codes",
        rsi_table=frag(RSI_TABLE),
        sec_type=SEC_TYPE_CODE[sec_type],
    )
    rows = await conn.fetch(view.sql, *view.params)
    return {r["code"] for r in rows}
