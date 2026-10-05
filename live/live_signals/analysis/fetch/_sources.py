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

mov_rsi is the only signal family since the 2026-10-05 retirement of
the mov_std / pair-cross strategies (the tech-stats legs / σ fetchers
went with them).
"""
from __future__ import annotations

import datetime

from asyncpg import Connection

from analyze.mov_ave_spread.steps.rsi import RSI_WINDOWS

from live.live_signals.config import RSI_TABLE
from live.live_signals.analysis.fetch._values import (
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
        sec_type, code, as_of,
    )
    if row is None:
        return {}
    return {w: row[f"rsi_{w}"] for w in RSI_WINDOWS
            if row[f"rsi_{w}"] is not None}


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
    return values
