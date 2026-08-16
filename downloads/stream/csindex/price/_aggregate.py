"""Aggregation: ~15s csindex ticks → 5-minute OHLC bars.

Uses the SAME ceiling convention as SSE (ceiling_5min) and SZSE so all
three streamers share the identical 5-min grid (09:35, 09:40, ..., 15:00).
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Dict, List, Optional, Tuple


def _parse_trade_time(time_str: str) -> Optional[time]:
    """Parse 'HH:MM:SS' or 'HH:MM' into a datetime.time."""
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(time_str.strip(), fmt).time()
        except ValueError:
            continue
    return None


def _window_end_5min(t: time) -> time:
    """Return the 5-minute window END time for a given tick time (ceiling).

    09:30 -> 09:30, 09:31-09:35 -> 09:35, 09:36-09:40 -> 09:40, ...

    Matches the SSE/SZSE ceiling convention so all three streamers are on
    the same 5-min grid.
    """
    minute = t.hour * 60 + t.minute
    wend_minute = ((minute - 1) // 5 + 1) * 5
    h, m = divmod(wend_minute, 60)
    return time(h, m)


def aggregate_ticks_to_5min(
    code: str,
    name: str,
    ticks: List[Dict[str, Any]],
    trade_date: date,
) -> Tuple[List[dict], List[dict], Optional[time]]:
    """Aggregate ~15s intraday ticks into 5-minute OHLC bars.

    Uses the ``current`` field (real-time price at each tick) for OHLC.
    The API's ``high``/``low`` fields are cumulative day running values
    (NOT per-tick), so they are NOT used for bar high/low.

    Returns (identity_rows, bar_rows, latest_bar_time).
    """
    if not ticks:
        return [], [], None

    # Group ticks by 5-min window end time
    windows: Dict[time, List[float]] = {}
    for tick in ticks:
        time_str = tick.get("tradeTime") or ""
        t = _parse_trade_time(str(time_str))
        if t is None:
            continue
        try:
            price = float(tick.get("current") or 0)
        except (ValueError, TypeError):
            continue
        if price <= 0:
            continue
        wend = _window_end_5min(t)
        windows.setdefault(wend, []).append(price)

    if not windows:
        return [], [], None

    latest_bar_time = max(windows.keys())

    identity_rows: List[dict] = []
    bar_rows: List[dict] = []
    for wend, prices in sorted(windows.items()):
        o = prices[0]
        h = max(prices)
        low = min(prices)
        c = prices[-1]
        change = round(c - o, 4)
        change_pct = round((c - o) / o * 100, 4) if o else None

        bar_rows.append({
            "date": trade_date,
            "code": code,
            "time": wend,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "change": change,
            "change_pct": change_pct,
        })

    identity_rows.append({
        "date": trade_date,
        "code": code,
        "name": name,
    })

    return identity_rows, bar_rows, latest_bar_time
