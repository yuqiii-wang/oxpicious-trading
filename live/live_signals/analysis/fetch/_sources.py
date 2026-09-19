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

from asyncpg import Connection

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
    _RSI_ROW_KINDS,
    _SPREAD_KINDS,
    SIGNAL_VALUE_SOURCE,
    LiveValues,
)

# Spread + state sources (the daily registries the day-close mirror
# audits against).
_SPREAD_TABLE = "analysis.mov_ave_spreads_detail"
_EMA_SPREAD_TABLE = "analysis.mov_ave_spreads_detail_ema"
_PX_VOL_TABLE = "analysis.mov_ave_price_vs_amt"

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


async def _fetch_latest_spreads(
    conn: Connection, sec_type: str, code: str,
    as_of: datetime.date | None = None,
) -> dict[str, float]:
    """The latest relative spreads (pair_{W} / ema_pair_{W} /
    px_pair_{W} / px_ema_pair_{W} — the MA5-vs-MA, EMA6-vs-EMA,
    close-vs-MA and close-vs-EMA families), keyed by the
    LiveValues.spread names. ``as_of`` bounds to the latest row
    at-or-before D."""
    ma_cols = ", ".join(
        f"ma5_vs_ma{w}::float8 AS pair_{w}" for w in MOV_PAIRS_WINDOWS
    )
    px_cols = ", ".join(
        f"price_vs_ma{w}::float8 AS px_pair_{w}" for w in MOV_PAIRS_WINDOWS
    )
    ema_cols = ", ".join(
        f"ema6_vs_ema{w}::float8 AS ema_pair_{w}"
        for w in MOV_PAIRS_EMA_WINDOWS
    )
    px_ema_cols = ", ".join(
        f"price_vs_ema{w}::float8 AS px_ema_pair_{w}"
        for w in MOV_PAIRS_EMA_WINDOWS
    )
    bound = "AND ($3::date IS NULL OR date <= $3::date)"
    ma_row = await conn.fetchrow(
        f"SELECT {ma_cols}, {px_cols} FROM {_SPREAD_TABLE} "
        f"WHERE sec_type = $1 AND code = $2 {bound} "
        f"ORDER BY date DESC LIMIT 1",
        sec_type, code, as_of,
    )
    ema_row = await conn.fetchrow(
        f"SELECT {ema_cols}, {px_ema_cols} FROM {_EMA_SPREAD_TABLE} "
        f"WHERE sec_type = $1 AND code = $2 {bound} "
        f"ORDER BY date DESC LIMIT 1",
        sec_type, code, as_of,
    )
    spread: dict[str, float] = {}
    for row in (ma_row, ema_row):
        if row is None:
            continue
        for k, v in row.items():
            if v is not None:
                spread[k] = v
    return spread


async def _fetch_latest_px_t(
    conn: Connection, sec_type: str, code: str,
    as_of: datetime.date | None = None,
) -> float | None:
    """The latest recorded px_t from the price_vs_amt registry
    (``as_of``-bounded for the on-demand replay)."""
    row = await conn.fetchrow(
        f"SELECT px_t::float8 AS px_t FROM {_PX_VOL_TABLE} "
        f"WHERE sec_type = $1 AND code = $2 AND px_t IS NOT NULL "
        f"  AND ($3::date IS NULL OR date <= $3::date) "
        f"ORDER BY date DESC LIMIT 1",
        sec_type, code, as_of,
    )
    return None if row is None else row["px_t"]


async def _fetch_latest_margin_z(
    conn: Connection, sec_type: str, code: str,
    as_of: datetime.date | None = None,
) -> float | None:
    """The latest margin-ratio z-score, recomputed by SQL window
    functions over EXACTLY the frame-row space and definition the
    detection used (ratio = rz_buy / trading_amount on rz_buy > 0
    days; 1220-row trailing moments shifted 1 row, min 250 non-NULL —
    per code, latest defined row). ``as_of`` bounds the frame to rows
    at-or-before D (the on-demand replay).
    Index has no margin data — None."""
    if sec_type not in _MARGIN_SOURCE:
        return None
    base, est = _MARGIN_SOURCE[sec_type]
    w = MARGIN_RATIO_Z_WINDOW
    mp = MARGIN_RATIO_Z_MIN_PERIODS
    row = await conn.fetchrow(
        f"""
        WITH frame AS (
            SELECT b.date,
                   CASE WHEN m.rz_buy > 0 AND m.trading_amount > 0
                        THEN m.rz_buy::float8
                             / NULLIF(m.trading_amount::float8, 0)
                   END AS ratio
            FROM {base}
            WHERE b.code = $1 AND b.close IS NOT NULL
              AND ($2::date IS NULL OR b.date <= $2::date)
              {est}
        ), z0 AS (
            SELECT date,
                   (ratio - avg(ratio) OVER w)
                   / NULLIF(stddev_samp(ratio) OVER w, 0) AS z,
                   count(ratio) OVER w AS n
            FROM frame
            WINDOW w AS (
                ORDER BY date
                ROWS BETWEEN {w} PRECEDING AND 1 PRECEDING
            )
        )
        SELECT z::float8 AS z FROM z0
        WHERE z IS NOT NULL AND n >= {mp}
        ORDER BY date DESC LIMIT 1
        """,
        code, as_of,
    )
    return None if row is None else row["z"]


async def _fetch_latest_ma_std(
    conn: Connection, sec_type: str, code: str,
    as_of: datetime.date | None = None,
) -> tuple[dict[int, float], dict[int, float]]:
    """Latest daily ma_{W} (stats.{sec}_tech_stats) and σ of price
    std_{W}days (analysis.mov_ave_spreads_detail) — the Bollinger
    band inputs the mov_std thresholds derive from (the bands move
    daily; the strategy's stored bar is never compared). ``as_of``
    bounds to the latest row at-or-before D — the band is derived from
    THE DAY'S OWN ma/σ, never a later snapshot's."""
    ma_cols = ", ".join(
        f"t.ma{w}::float8 AS ma{w}" for w in MA_WINDOWS
    )
    std_cols = ", ".join(
        f"d.std_{w}days::float8 AS std{w}" for w in MA_WINDOWS
    )
    row = await conn.fetchrow(
        f"SELECT {ma_cols}, {std_cols} "
        f"FROM stats.{sec_type}_tech_stats t "
        f"LEFT JOIN {_SPREAD_TABLE} d "
        f"  ON d.sec_type = $1 AND d.code = t.code AND d.date = t.date "
        f"WHERE t.code = $2 "
        f"  AND ($3::date IS NULL OR t.date <= $3::date) "
        f"ORDER BY t.date DESC LIMIT 1",
        sec_type, code, as_of,
    )
    if row is None:
        return {}, {}
    ma = {w: row[f"ma{w}"] for w in MA_WINDOWS
          if row[f"ma{w}"] is not None}
    std = {w: row[f"std{w}"] for w in MA_WINDOWS
           if row[f"std{w}"] is not None}
    return ma, std


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
    if kinds & _SPREAD_KINDS:
        values.spread = await _fetch_latest_spreads(
            conn, sec_type, code, as_of,
        )
    if "px_t" in kinds:
        values.px_t = await _fetch_latest_px_t(conn, sec_type, code, as_of)
    if "margin_z" in kinds:
        values.margin_z = await _fetch_latest_margin_z(
            conn, sec_type, code, as_of,
        )
    if "mov_std" in signal_types:
        values.ma, values.std = await _fetch_latest_ma_std(
            conn, sec_type, code, as_of,
        )
    return values
