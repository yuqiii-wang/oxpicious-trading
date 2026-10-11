"""Current-value source fetchers of the live breach check
(live.live_signals.analysis.fetch._sources).

Everything here is DATA ACCESS, not signal logic — the latest value of
each source kind for ONE code, each queried at most once per check and
bounded as-of. fetch_current_values routes by the value-source map
(_values): only the sources the active signal types actually consume
are queried.

AS-OF MODE: every ``as_of`` parameter bounds the fetch to the latest
row AT OR BEFORE D — the value sources are daily tables, so the
as-of evaluation replays "what the live check would have seen on D".

The bollinger family's current σ-depth z is computed ON THE FLY from
the code's last closes (2026-10-10 — the analysis.mov_ave_boll
value-source table is gone; the family's signal values are in-engine
everywhere now). The daily z of the latest close per window is the
classic trailing construction the analysis_signals engine writes —
population σ, min_periods=W, degenerate σ skipped (never invented).
"""
from __future__ import annotations

import datetime
import statistics

from asyncpg import Connection

from _common.labels import SEC_TYPE_CODE
from analysis.mov_ave_spread.config.price_sources import PRICE_SOURCE
from analysis.mov_ave_spread.steps.rsi import RSI_WINDOWS
from analysis.analysis_signals.config import (
    BOLL_SIGMA_FLOOR,
    BOLL_WINDOWS,
)

from live.live_signals.config import RSI_TABLE
from live.live_signals.analysis.fetch._values import (
    _BOLL_ROW_KINDS,
    _RSI_ROW_KINDS,
    SIGNAL_VALUE_SOURCE,
    LiveValues,
)


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
        # analysis.mov_ave_rsi.sec_type stores its stats.label_dim
        # SMALLINT code (the label-code contract) — the run's sec_type
        # label maps once here.
        SEC_TYPE_CODE[sec_type], code, as_of,
    )
    if row is None:
        return {}
    return {w: row[f"rsi_{w}"] for w in RSI_WINDOWS
            if row[f"rsi_{w}"] is not None}


async def _fetch_latest_boll(
    conn: Connection, sec_type: str, code: str,
    as_of: datetime.date | None = None,
) -> dict[int, float]:
    """The code's current Bollinger σ-depth z per window, computed from
    its last closes (the mov_ave_spread price convention — ETF adjusted
    close): the LATEST close's depth z = (close − SMA_W) / σ_W, the
    population σ over the trailing W closes, min_periods=W (fewer rows
    → no value) and degenerate σ skipped. ``as_of`` bounds the price
    history to rows at-or-before D (the value the daily z had then)."""
    base, price_expr = PRICE_SOURCE[sec_type]
    rows = await conn.fetch(
        f"SELECT {price_expr}::float8 AS price "
        f"FROM {base} "
        f"WHERE b.code = $1 AND b.close IS NOT NULL "
        f"  AND ($2::date IS NULL OR b.date <= $2::date) "
        f"ORDER BY b.date DESC "
        f"LIMIT {max(BOLL_WINDOWS)}",
        code, as_of,
    )
    if not rows:
        return {}
    # ascending: oldest .. latest (the latest close is the depth anchor)
    prices = [r["price"] for r in rows][::-1]
    out: dict[int, float] = {}
    for w in BOLL_WINDOWS:
        if len(prices) < w:
            continue
        window = prices[-w:]
        sigma = statistics.pstdev(window)
        if sigma is None or sigma <= BOLL_SIGMA_FLOOR:
            continue
        out[w] = (prices[-1] - statistics.fmean(window)) / sigma
    return out


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
    if kinds & _BOLL_ROW_KINDS:
        values.boll = await _fetch_latest_boll(
            conn, sec_type, code, as_of,
        )
    return values
