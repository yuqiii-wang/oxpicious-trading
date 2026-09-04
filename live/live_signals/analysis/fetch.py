"""DB fetchers specific to the analysis signal scheme."""
from __future__ import annotations

from analyze.analysis_forecasts.config import RSI_WINDOWS

from live.live_signals.config import RSI_TABLE, SIGNALS_TABLE


async def fetch_active_signals(conn, sec_type: str, code: str) -> list[dict]:
    """The code's ACTIVE signal configs (current threshold set): the
    is_active rows of analysis_signals.signals for the resolved
    sec_type — all signal types / sub types. confidence is returned
    already on the live 0-100 scale: ROUND(confidence * 100)::int of
    the source's reverse_prob probability (exact NUMERIC rounding —
    do NOT re-scale in Python, float math would round ties differently)."""
    rows = await conn.fetch(
        f"SELECT code, sec_type, signal_type, signal_sub_type, date, "
        f"       action, signal_threshold::float8 AS signal_threshold, "
        f"       ROUND(COALESCE(confidence, 0) * 100)::int AS confidence, "
        f"       params "
        f"FROM {SIGNALS_TABLE} "
        f"WHERE sec_type = $1 AND code = $2 AND is_active "
        f"ORDER BY signal_type, signal_sub_type",
        sec_type, code,
    )
    return [dict(r) for r in rows]


async def fetch_current_rsii(
    conn, sec_type: str, code: str,
) -> dict[int, float]:
    """Latest daily RSI per window from analysis.mov_ave_rsi (one row).

    Returns {window: rsi_value} — windows with a NULL value on the
    latest row are absent (not comparable that day)."""
    cols = ", ".join(f"rsi_{w}days::float8 AS rsi_{w}" for w in RSI_WINDOWS)
    row = await conn.fetchrow(
        f"SELECT {cols} FROM {RSI_TABLE} "
        f"WHERE sec_type = $1 AND code = $2 "
        f"ORDER BY date DESC LIMIT 1",
        sec_type, code,
    )
    if row is None:
        return {}
    return {
        w: row[f"rsi_{w}"] for w in RSI_WINDOWS
        if row[f"rsi_{w}"] is not None
    }


async def fetch_active_codes(conn, sec_type: str) -> list[str]:
    """Codes with ACTIVE signal configs for the sec_type (the batch
    mode's universe: every code whose thresholds are live)."""
    rows = await conn.fetch(
        f"SELECT DISTINCT code FROM {SIGNALS_TABLE} "
        f"WHERE sec_type = $1 AND is_active ORDER BY code",
        sec_type,
    )
    return [r["code"] for r in rows]
