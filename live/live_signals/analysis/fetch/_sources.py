"""Current-value source fetchers of the live breach check
(live.live_signals.analysis.fetch._sources).

Everything here is DATA ACCESS, not signal logic — the latest value of
each source kind for ONE code (the registries the day-close mirror
audits against), each queried at most once per check and bounded
as-of. fetch_current_values routes by the value-source map (_values):
only the sources the active signal types actually consume are
queried.

AS-OF MODE: every ``as_of`` parameter bounds the fetch to the latest
row AT OR BEFORE D — the value sources are daily tables, so the
as-of evaluation replays "what the live check would have seen on D".
"""
from __future__ import annotations

import datetime

import numpy as np
import pandas as pd
from asyncpg import Connection

from _common.build_commons import rec_cols
from _common.df_utils import host_array

from analyze.analysis_forecasts.config import (
    MA_WINDOWS,
    MARGIN_RATIO_Z_MIN_PERIODS,
    MARGIN_RATIO_Z_WINDOW,
    MOV_PAIRS_EMA_WINDOWS,
    MOV_PAIRS_WINDOWS,
    RSI_WINDOWS,
)

from live.live_signals.config import RSI_TABLE
from live.live_signals.analysis.fetch._values import (
    _CROSS_KINDS,
    _PAIR_FAST_EMA,
    _RSI_ROW_KINDS,
    SIGNAL_VALUE_SOURCE,
    LiveValues,
)

# Spread + state sources (the daily registries the day-close mirror
# audits against).
_SPREAD_TABLE = "analysis.mov_ave_spreads_detail"

# margin-ratio source table + the estimated-close row filter per
# sec_type (the frame-row space the detection z windows were computed
# on).
_MARGIN_SOURCE: dict[str, tuple[str, str]] = {
    "etf": (
        "stats.etf_basic_stats b "
        "LEFT JOIN stats.etf_liquidity_margin m "
        "ON m.code = b.code AND m.date = b.date",
        "AND COALESCE(b.is_close_estimated, FALSE) = FALSE",
    ),
    "stock": (
        "stats.stock_basic_stats b "
        "LEFT JOIN stats.stock_liquidity_margin m "
        "ON m.code = b.code AND m.date = b.date",
        "",
    ),
}


async def _fetch_latest_indicators(
    conn: Connection, sec_type: str, code: str,
    as_of: datetime.date | None = None,
) -> dict[int, float]:
    """The latest rsi_{W} (analysis.mov_ave_rsi), keyed by window.
    The stored column is rsi_{W}days — aliased to the bare window key.
    ``as_of`` bounds to the latest row at-or-before D."""
    cols = ", ".join(f"rsi_{w}days::float8 AS rsi_{w}" for w in RSI_WINDOWS)
    row = await conn.fetchrow(
        f"SELECT {cols} FROM {RSI_TABLE} "
        f"WHERE sec_type = $1 AND code = $2 "
        f"  AND ($3::date IS NULL OR date <= $3::date) "
        f"ORDER BY date DESC LIMIT 1",
        sec_type, code, as_of,
    )
    if row is None:
        return {}
    return {w: row[f"rsi_{w}"] for w in RSI_WINDOWS
            if row[f"rsi_{w}"] is not None}


async def _fetch_latest_ma_ema_std(
    conn: Connection, sec_type: str, code: str,
    as_of: datetime.date | None = None,
) -> tuple[dict[int, float], dict[int, float], dict[int, float],
           datetime.date | None]:
    """Latest daily ma_{W} + ema_{W} legs (stats.{sec}_tech_stats) and
    σ of price std_{W}days (analysis.mov_ave_spreads_detail) — the
    mov_std band inputs AND the cross-family legs (all from ONE
    tech_stats row; bands and cross legs move daily — the strategies'
    stored bars are never compared). The ma keys cover the mov_std
    windows plus the pair slow legs; the ema keys cover the ema6 fast
    leg plus the pair slow legs. ``as_of`` bounds to the latest row
    at-or-before D — the values come from THE DAY'S OWN row, never a
    later snapshot's. Returns (ma, ema, std, legs_date) — legs_date is
    that row's date (the derived-bar families' state basis; the
    is_triggered_once episode gate steps one row earlier from it)."""
    ma_ws = sorted(set(MA_WINDOWS) | set(MOV_PAIRS_WINDOWS))
    ema_ws = sorted({_PAIR_FAST_EMA, *MOV_PAIRS_EMA_WINDOWS})
    ma_cols = ", ".join(f"t.ma{w}::float8 AS ma{w}" for w in ma_ws)
    ema_cols = ", ".join(f"t.ema{w}::float8 AS ema{w}" for w in ema_ws)
    std_cols = ", ".join(
        f"d.std_{w}days::float8 AS std{w}" for w in MA_WINDOWS
    )
    row = await conn.fetchrow(
        f"SELECT t.date AS legs_date, {ma_cols}, {ema_cols}, {std_cols} "
        f"FROM stats.{sec_type}_tech_stats t "
        f"LEFT JOIN {_SPREAD_TABLE} d "
        f"  ON d.sec_type = $1 AND d.code = t.code AND d.date = t.date "
        f"WHERE t.code = $2 "
        f"  AND ($3::date IS NULL OR t.date <= $3::date) "
        f"ORDER BY t.date DESC LIMIT 1",
        sec_type, code, as_of,
    )
    if row is None:
        return {}, {}, {}, None
    ma = {w: row[f"ma{w}"] for w in ma_ws if row[f"ma{w}"] is not None}
    ema = {w: row[f"ema{w}"] for w in ema_ws
           if row[f"ema{w}"] is not None}
    std = {w: row[f"std{w}"] for w in MA_WINDOWS
           if row[f"std{w}"] is not None}
    return ma, ema, std, row["legs_date"]


async def _fetch_latest_margin_z(
    conn: Connection, sec_type: str, code: str,
    as_of: datetime.date | None = None,
) -> float | None:
    """The latest margin-ratio z-score, recomputed in pandas over
    EXACTLY the frame-row space and definition the detection used
    (analyze.analysis_forecasts.fetch.margin.add_margin_ratio_features):
    ratio = rz_buy / trading_amount on rz_buy > 0 days; 1220-row
    trailing sample moments shifted 1 row, min 250 non-NULL — per code,
    latest defined row. ``as_of`` bounds the frame to rows at-or-before
    D (the on-demand replay). Index has no margin data — None.

    The former SQL window-form (CASE WHEN ratio + avg/stddev_samp OVER
    ROWS BETWEEN w PRECEDING AND 1 PRECEDING + a ``n >= min_periods``
    post-filter) selects the same row: pandas' min_periods=mp on the
    shifted moments makes a defined z IMPLY count >= mp, so z-notna is
    the exact former ``z IS NOT NULL AND n >= mp`` predicate.
    """
    if sec_type not in _MARGIN_SOURCE:
        return None
    base, est = _MARGIN_SOURCE[sec_type]
    w = MARGIN_RATIO_Z_WINDOW
    mp = MARGIN_RATIO_Z_MIN_PERIODS
    rows = await conn.fetch(
        f"""
        SELECT m.rz_buy::float8          AS rz_buy,
               m.trading_amount::float8  AS trading_amount
        FROM {base}
        WHERE b.code = $1 AND b.close IS NOT NULL
          AND ($2::date IS NULL OR b.date <= $2::date)
          {est}
        ORDER BY b.date
        """,
        code, as_of,
    )
    if not rows:
        return None
    df = pd.DataFrame(
        rec_cols(rows), columns=["rz_buy", "trading_amount"],
    )
    ta = df["trading_amount"].astype(float)
    rb = df["rz_buy"].astype(float)
    # Pure-boolean guards (the detection's cudf.pandas discipline —
    # comparisons on NULL-bearing series are filled BEFORE the compare):
    # ta_pos = trading_amount NOT NULL AND > 0; rb_buy = rz_buy NOT NULL
    # AND > 0.
    ta_pos = ta.fillna(0.0) > 0
    rb_buy = rb.fillna(-1.0) > 0
    ratio = (rb / ta).where(rb_buy & ta_pos)

    # Trailing w-row sample moments SHIFTED 1 row (yesterday's moments
    # are today's bars — no look-ahead), min_periods=mp non-NULL — the
    # detection's grouped_rolling_agg(min_periods=mp) + grouped_shift(1)
    # for a one-code frame (single group ⇒ plain rolling is identical).
    mu = ratio.rolling(w, min_periods=mp).mean()
    sig = ratio.rolling(w, min_periods=mp).std(ddof=1)
    sig_l = sig.shift(1)
    z = ((ratio - mu.shift(1)) / sig_l).where(sig_l.fillna(0.0) > 0)

    z_arr = host_array(z.to_numpy())
    valid = np.flatnonzero(~np.isnan(z_arr))
    return float(z_arr[valid[-1]]) if valid.size else None


async def fetch_current_values(
    conn: Connection,
    sec_type: str,
    code: str,
    signal_types: set[str],
    as_of: datetime.date | None = None,
) -> LiveValues:
    """The code's current values for the given active signal types —
    each source queried at most once (the source kinds derived from
    THE value-source map; only what the active types consume is
    queried). ``as_of`` bounds every source to its latest row at-or-
    before D (the on-demand replay basis)."""
    kinds = {SIGNAL_VALUE_SOURCE[t] for t in signal_types
             if t in SIGNAL_VALUE_SOURCE}
    values = LiveValues()
    if kinds & _RSI_ROW_KINDS:
        values.rsi = await _fetch_latest_indicators(
            conn, sec_type, code, as_of,
        )
    if "mov_std" in signal_types or kinds & _CROSS_KINDS:
        (values.ma, values.ema, values.std,
         values.legs_date) = await _fetch_latest_ma_ema_std(
            conn, sec_type, code, as_of,
        )
    if "margin_z" in kinds:
        values.margin_z = await _fetch_latest_margin_z(
            conn, sec_type, code, as_of,
        )
    return values
