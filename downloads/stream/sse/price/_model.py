"""SSE stream model — AssetStream context, OHLCV aggregation, trading-hours.

Shared by the stock flow (``_stock.py``) and the index flow (``_index.py``).
``AssetStream`` encapsulates everything that differs between the equity and
index tabs (list endpoint, target tables, code format, volume handling); the
generic ``aggregate_bars`` reads/writes the per-asset mutable state (sample
buffer, cumulative-volume baseline, finished-codes set) to produce identity +
bar rows for either intraday table.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from downloads._common import (
    add_exchange_suffix,
    is_trading_day,
    setup_logger,
)

logger = setup_logger("stream_sse")

# A snapshot sample: {bare_code: full_record} where full_record holds every
# field returned by the SSE list endpoint (name, open, high, low, last,
# prev_close, change, volume, amount). Carrying the full row lets us both
# aggregate OHLCV (last + volume) and archive the raw queried data to CSV.
Snapshot = Dict[str, dict]

TRADING_SESSIONS: List[Tuple[time, time]] = [
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
]

CLOSE_TIME = time(15, 0)

DEFAULT_POLL_INTERVAL_SEC = 60
DEFAULT_BAR_WINDOW = 5


def ceiling_5min(t: time) -> time:
    """Snap a time to the END of its 5-minute window (ceiling convention).

    Matches downloads.stream.szse.price._aggregation._window_end_minute so
    SSE, SZSE, and CSIndex all produce bars on the same 5-min grid
    (09:35, 09:40, ..., 15:00). A tick at 09:35 closes the 09:35 bar
    (NOT 09:40); a tick at 09:34 also maps to 09:35.

    09:30 -> 09:30  (boundary stays — only matters for the open tick;
                     with bar_window=5 the last sample is at 09:34 or 09:35,
                     so the first bar is 09:35, never 09:30)
    09:31 -> 09:35, 09:34 -> 09:35, 09:35 -> 09:35, 09:36 -> 09:40, ...
    """
    minute = t.hour * 60 + t.minute
    wend = ((minute - 1) // 5 + 1) * 5
    return time(wend // 60, wend % 60)

# Local CSV archive: one file per trading day, appended to on every poll,
# under temps/<subdir>/.
CSV_COLUMNS = [
    "update_time", "code", "name", "open", "high", "low", "last",
    "prev_close", "change", "volume", "amount",
]


@dataclass
class AssetStream:
    """Per-asset-type streaming context (股票 / 基金 / 指数).

    Encapsulates everything that differs between the equity, fund, and index
    tabs of the SSE report page: the list endpoint, target tables, code
    format, whether per-bar volume is recorded, the optional "only load
    existing codes" filter, and the mutable streaming state (sample buffer,
    cumulative-volume baseline, finished-codes set).

    Attributes:
        name: 'stock', 'etf', or 'index'.
        list_url: SSE list endpoint for this type.
        identity_table: FK parent table (e.g. stats.stock_identity).
        intraday_table: bar table (e.g. stats.stock_intraday_5min).
        exchange: canonical exchange code appended to codes ('SS' for
            stocks/ETFs), or None for indices (bare 6-digit code per
            index_identity CHECK).
        has_volume: stocks/ETFs record per-bar volume; indices do not
            (index_intraday_5min has no volume column).
        allowed_codes: optional allow-list of bare codes. When set (indices),
            snapshot rows whose code is NOT in this set are dropped before
            entering the buffer — implements "only load to existing index".
        csv_subdir: subdirectory under temps/ for the daily CSV archive.
        csv_prefix: filename prefix for the daily CSV archive.
        buffer: list of (update_dt, snapshot) samples collected this bar.
        prev_bar_cumvol: bare_code -> cumulative volume at the end of the
            previous bar. Stocks/ETFs only (indices have no volume).
        finished_codes: bare codes that already reached CLOSE_TIME today.
    """
    name: str
    list_url: str
    identity_table: str
    intraday_table: str
    exchange: Optional[str]
    has_volume: bool
    allowed_codes: Optional[set] = None
    csv_subdir: str = "sse_intraday"
    csv_prefix: str = "sse_intraday"
    buffer: List[Tuple[datetime, "Snapshot"]] = field(default_factory=list)
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
    asset: AssetStream,
    trade_date,
    etf_member_codes: Optional[set] = None,
) -> Tuple[List[dict], List[dict], Optional[time]]:
    """Aggregate 5 one-minute samples into per-security OHLCV bars.

    Drives off the ``asset`` context (AssetStream): stock / ETF bars carry a
    per-bar volume + exchange and land in stats.stock_intraday_5min /
    stats.etf_intraday_5min (code WITH exchange suffix, e.g. "600000.SS");
    index bars carry NO volume / exchange and land in
    stats.index_intraday_5min (bare 6-digit code). The ``asset.buffer`` /
    ``prev_bar_cumvol`` / ``finished_codes`` mutable state is read and updated
    in place.

    Args:
        asset: AssetStream whose buffer holds the samples to aggregate.
        trade_date: datetime.date for the bars.
        etf_member_codes: optional set of full codes (e.g. "600000.SS") that
            are currently in any ETF (latest sec_composition snapshot,
            weight_pct > 0.1). Stocks only: identity rows get
            is_in_index_or_etf=full_code in etf_member_codes; ignored for ETFs
            (etf_identity has no such column) and indices.

    Returns (identity_rows, bar_rows, bar_time).
      * identity_rows feed asset.identity_table (satisfies the FK).
      * bar_rows feed asset.intraday_table.
    """
    buffer = asset.buffer
    if not buffer:
        return [], [], None

    last_dt = buffer[-1][0]
    # Bar end time = last sample's clock time snapped to the 5-min grid
    # (ceiling convention, same as SZSE _window_end_minute). This ensures
    # SSE bars land on 09:35/09:40/.../15:00, NOT 09:34/09:39/... which
    # would happen if we used the raw poll timestamp.
    bar_time = ceiling_5min(last_dt.time())

    # Collect every code seen across the window.
    all_codes: set = set()
    for _, snap in buffer:
        all_codes.update(snap.keys())

    # Stocks AND ETFs share the same row layout (code WITH exchange suffix,
    # per-bar trading_shares derived from cumulative volume delta). Indices
    # use bare 6-digit codes with no volume / exchange columns.
    is_suffixed = asset.exchange is not None
    is_stock = asset.name == "stock"  # only stock_identity has is_in_index_or_etf
    identity_rows: List[dict] = []
    bar_rows: List[dict] = []
    n_skipped = 0
    for code in sorted(all_codes):
        # Skip securities that have already reached CLOSE_TIME for this trade_date.
        if code in asset.finished_codes:
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
            if last is not None:
                lasts.append(last)
            if cumvol is not None:
                cumvols.append(cumvol)
            nm = entry.get("name") or ""
            if nm:
                name = nm
        if not lasts:
            # Suspended / no-trade security in this window: still track cumvol
            # (stocks/ETFs only — indices have no volume baseline).
            if asset.has_volume and cumvols:
                asset.prev_bar_cumvol[code] = cumvols[-1]
            continue

        o = lasts[0]
        h = max(lasts)
        low = min(lasts)
        c = lasts[-1]

        change = round(c - o, 4)
        change_pct = round((c - o) / o * 100, 4) if o else None

        if is_suffixed:
            # Stocks / ETFs: derive per-bar volume from cumulative volume delta,
            # and store code WITH exchange suffix (e.g. "600000.SS" / "510050.SS").
            end_cumvol = cumvols[-1] if cumvols else 0.0
            prev_cumvol = asset.prev_bar_cumvol.get(code, 0.0)
            vol = end_cumvol - prev_cumvol
            if vol < 0:
                # Cumulative volume should never decrease; if it does (e.g. a
                # new trading day rolled over without a reset), trust the new
                # value.
                vol = end_cumvol
            asset.prev_bar_cumvol[code] = end_cumvol

            full_code = add_exchange_suffix(code, "上海")
            # Canonical exchange from the asset context (SS for SSE streams).
            exchange = asset.exchange
            identity_row = {"date": trade_date, "code": full_code, "exchange": exchange, "name": name}
            # Only stock_identity has the is_in_index_or_etf column; etf_identity
            # does NOT (would cause a missing-column INSERT error).
            if is_stock and etf_member_codes is not None:
                identity_row["is_in_index_or_etf"] = full_code in etf_member_codes
            identity_rows.append(identity_row)
            bar_rows.append({
                "date": trade_date,
                "code": full_code,
                "exchange": exchange,
                "time": bar_time,
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "trading_shares": vol,
                "change": change,
                "change_pct": change_pct,
            })
        else:
            # Indices: bare 6-digit code, NO volume / exchange columns
            # (index_intraday_5min schema). Codes are already filtered to the
            # existing-index allow-list in fetch_snapshot.
            identity_rows.append({
                "date": trade_date,
                "code": code,
                "name": name,
            })
            bar_rows.append({
                "date": trade_date,
                "code": code,
                "time": bar_time,
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "change": change,
                "change_pct": change_pct,
            })

        # Mark this security as finished if the bar reaches CLOSE_TIME.
        if bar_time >= CLOSE_TIME:
            asset.finished_codes.add(code)

    if n_skipped > 0:
        logger.debug(
            "aggregate_bars(%s): skipped %d already-finished codes (bar_time=%s)",
            asset.name, n_skipped, bar_time,
        )

    return identity_rows, bar_rows, bar_time
