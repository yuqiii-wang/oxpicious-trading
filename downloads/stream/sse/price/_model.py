"""SSE stream model — AssetStream context, OHLCV aggregation, trading-hours.

Shared by the stock flow (``_stock.py``) and the index flow (``_index.py``).
``AssetStream`` encapsulates everything that differs between the equity and
index tabs (list endpoint, target tables, code format, volume handling); its
``aggregate_bars`` method (the former module-level ``aggregate_bars(asset, ...)``
mutator, self = the asset) reads/writes the per-asset mutable state (sample
buffer, cumulative-volume baseline, finished-codes set) to produce identity +
bar rows for either intraday table. Trading-hours helpers stay module-level
(pure functions).
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from downloads._common import (
    add_exchange_suffix,
    is_trading_day,
    poll_interval_sec_for,
    setup_logger,
)

from downloads.stream.sse.price.data_definitions import (
    AssetStream,
    CLOSE_TIME,
    Snapshot,
    ceiling_5min,
)
logger = setup_logger("stream_sse")

SERVICE = "downloads.stream.sse.price"



TRADING_SESSIONS: List[Tuple[time, time]] = [
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
]



# Poll-loop cadence — resolved once at import from the service's
# external_request_config JSON row (request_type "poll"); the row does not
# define a poll interval, so the old 60s literal stays as the fallback.
DEFAULT_POLL_INTERVAL_SEC = poll_interval_sec_for(SERVICE, "poll") or 60
DEFAULT_BAR_WINDOW = 5



# Local CSV archive: one file per trading day, appended to on every poll,
# under temps/<subdir>/.
CSV_COLUMNS = [
    "update_time", "code", "name", "open", "high", "low", "last",
    "prev_close", "change", "volume", "amount",
]




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
