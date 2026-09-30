"""BSE stream model — stream state, OHLCV aggregation, trading-hours.

Mirrors downloads/stream/sse/price/_model.py (kept per-package per the
sibling-streamer convention): the BSE quotation endpoint
(https://www.bse.cn/nqhqController/nqhq_en.do behind
https://www.bse.cn/nq/quotation.html) publishes full-universe snapshots
with today's CUMULATIVE volume, so per-bar volume is derived by
subtraction across samples — same semantics as the SSE equity flow.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from downloads._common import is_trading_day, setup_logger

logger = setup_logger("stream_bse")

TRADING_SESSIONS: List[Tuple[time, time]] = [
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
]

CLOSE_TIME = time(15, 0)

DEFAULT_POLL_INTERVAL_SEC = 60
DEFAULT_BAR_WINDOW = 5

# Local CSV archive: one file per trading day, appended on every sweep, under
# temps/bse_intraday/. Column schema matches the SSE intraday archive so a
# future CSV backfill can share code.
CSV_COLUMNS = [
    "update_time", "code", "name", "open", "high", "low", "last",
    "prev_close", "change", "volume", "amount",
]


def ceiling_5min(t: time) -> time:
    """Snap a time to the END of its 5-minute window (ceiling convention).

    Same grid as SSE/SZSE/CSIndex streamers (bars land on 09:35, 09:40, …,
    15:00). A tick at 09:35 closes the 09:35 bar, not 09:40.
    """
    minute = t.hour * 60 + t.minute
    wend = ((minute - 1) // 5 + 1) * 5
    return time(wend // 60, wend % 60)


@dataclass
class BseStream:
    """Mutable per-day streaming state for the BSE stock flow.

    Attributes:
        buffer: (update_dt, {bare_code: record}) samples collected for the
            bar currently being built.
        prev_bar_cumvol: bare_code -> cumulative volume (shares) at the end
            of the previous bar; per-bar volume is the delta against it.
        finished_codes: bare codes that already reached CLOSE_TIME today.
    """
    buffer: List[Tuple[datetime, Dict[str, dict]]] = field(default_factory=list)
    prev_bar_cumvol: Dict[str, float] = field(default_factory=dict)
    finished_codes: set = field(default_factory=set)


# ---------------------------------------------------------------------------
# Trading-hours helpers
# ---------------------------------------------------------------------------
def in_trading_hours(dt: datetime) -> bool:
    t = dt.time()
    for start, end in TRADING_SESSIONS:
        if start <= t <= end:
            return True
    return False


def next_trading_moment(now: datetime) -> datetime:
    """Return the next datetime at which trading is active (today or next trading day)."""
    d = now.date()
    t = now.time()
    if is_trading_day(d):
        if t < time(9, 30):
            return datetime.combine(d, time(9, 30))
        if time(9, 30) <= t <= time(11, 30):
            return now
        if time(11, 30) < t < time(13, 0):
            return datetime.combine(d, time(13, 0))
        if time(13, 0) <= t <= time(15, 0):
            return now
        # after 15:00 -> next trading day
    nd = d + timedelta(days=1)
    while not is_trading_day(nd):
        nd += timedelta(days=1)
    return datetime.combine(nd, time(9, 30))


def sleep_until(target_dt: datetime, chunk_sec: float = 60.0) -> None:
    """Sleep until target_dt, in chunks so KeyboardInterrupt stays responsive."""
    while True:
        now = datetime.now()
        if now >= target_dt:
            return
        remaining = (target_dt - now).total_seconds()
        _time.sleep(min(chunk_sec, max(0.0, remaining)))


# ---------------------------------------------------------------------------
# 5-minute OHLCV aggregation
# ---------------------------------------------------------------------------
def aggregate_bars(
    stream: BseStream,
    trade_date,
) -> Tuple[List[dict], List[dict], Optional[time]]:
    """Aggregate the buffered samples into per-stock OHLCV bars.

    open/high/low/close come from the sampled ``last`` prices (same
    convention as the SSE equity flow — intra-sample extremes are not
    visible to a snapshot poller); per-bar ``trading_shares`` is the delta
    of the endpoint's cumulative volume against the previous bar.

    Returns (identity_rows, bar_rows, bar_time) for
    stats.stock_identity / stats.stock_intraday_5min (code "NNNNNN.BJ").
    """
    buffer = stream.buffer
    if not buffer:
        return [], [], None

    bar_time = ceiling_5min(buffer[-1][0].time())

    all_codes: set = set()
    for _, snap in buffer:
        all_codes.update(snap.keys())

    identity_rows: List[dict] = []
    bar_rows: List[dict] = []
    n_skipped = 0
    for code in sorted(all_codes):
        if code in stream.finished_codes:
            n_skipped += 1
            continue

        lasts: List[float] = []
        cumvols: List[float] = []
        name = ""
        for _, snap in buffer:
            entry = snap.get(code)
            if entry is None:
                continue
            last = entry.get("last")
            cumvol = entry.get("volume")
            if last:
                lasts.append(last)
            if cumvol is not None:
                cumvols.append(cumvol)
            nm = entry.get("name") or ""
            if nm:
                name = nm
        if not lasts:
            # No trade this window (suspended / pre-open zero rows): still
            # advance the cumulative-volume baseline.
            if cumvols:
                stream.prev_bar_cumvol[code] = cumvols[-1]
            continue

        o = lasts[0]
        h = max(lasts)
        low = min(lasts)
        c = lasts[-1]

        change = round(c - o, 4)
        change_pct = round((c - o) / o * 100, 4) if o else None

        end_cumvol = cumvols[-1] if cumvols else 0.0
        prev_cumvol = stream.prev_bar_cumvol.get(code, 0.0)
        vol = end_cumvol - prev_cumvol
        if vol < 0:
            # Cumulative volume should never decrease; if it does (e.g. a
            # new trading day rolled over without a reset), trust the new
            # value.
            vol = end_cumvol
        stream.prev_bar_cumvol[code] = end_cumvol

        full_code = f"{code}.BJ"
        identity_rows.append({
            "date": trade_date,
            "code": full_code,
            "exchange": "BJ",
            "board": "BSE",
            "name": name,
            "is_in_index_or_etf": False,
        })
        bar_rows.append({
            "date": trade_date,
            "code": full_code,
            "exchange": "BJ",
            "time": bar_time,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "trading_shares": vol,
            "change": change,
            "change_pct": change_pct,
        })

        if bar_time >= CLOSE_TIME:
            stream.finished_codes.add(code)

    if n_skipped > 0:
        logger.debug(
            "aggregate_bars: skipped %d already-finished codes (bar_time=%s)",
            n_skipped, bar_time,
        )

    return identity_rows, bar_rows, bar_time


# ---------------------------------------------------------------------------
# Index (北证50) 5-minute aggregation — from per-minute realtime rows
# ---------------------------------------------------------------------------
def aggregate_index_bars(
    records: List[dict],
    trade_date,
    code: str = "899050",
    name: str = "北证50",
) -> Tuple[List[dict], List[dict], Optional[time]]:
    """Aggregate getRealBZ50 per-minute records into 5-minute OHLC bars.

    One call returns the whole session's minutes, so each poll re-aggregates
    EVERY window and the idempotent upsert refreshes them all — no buffering
    or baseline state, and a restart self-heals from any point. Bar OHLC
    comes from the per-minute index values (SSZS); ``index_intraday_5min``
    has no volume column, so CJL/CJJE are archived to CSV only.

    Returns (identity_rows, bar_rows, latest_minute_time).
    """
    windows: Dict[time, List[Tuple[time, float]]] = {}
    for rec in records or []:
        gxsj = str(rec.get("GXSJ") or "").strip()
        value = rec.get("SSZS")
        if len(gxsj) < 4 or value is None:
            continue
        try:
            t = time(int(gxsj[:2]), int(gxsj[2:4]))
        except ValueError:
            continue
        windows.setdefault(ceiling_5min(t), []).append((t, float(value)))

    if not windows:
        return [], [], None

    identity_rows: List[dict] = [{"date": trade_date, "code": code, "name": name}]
    bar_rows: List[dict] = []
    for wend in sorted(windows):
        samples = sorted(windows[wend], key=lambda s: s[0])
        values = [v for _, v in samples]
        o, c = values[0], values[-1]
        bar_rows.append({
            "date": trade_date,
            "code": code,
            "time": wend,
            "open": o,
            "high": max(values),
            "low": min(values),
            "close": c,
            "change": round(c - o, 4),
            "change_pct": round((c - o) / o * 100, 4) if o else None,
        })

    return identity_rows, bar_rows, max(windows)
