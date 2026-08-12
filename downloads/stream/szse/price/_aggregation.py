"""SZSE 5-minute OHLCV aggregation — stock and index bar builders.

Aggregates 1-minute samples (from any of the four API sources) into 5-minute
OHLCV bars. Stock bars include ``trading_shares`` and ``code_suffix``;
index bars omit both (matching the ``index_intraday_5min`` schema).
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Dict, List, Optional, Tuple

from downloads._common.core import add_exchange_suffix

from ._akshare_source import MinuteSample

# 15:00 (market close) expressed as minutes-of-day. Once the wall-clock
# reaches this, every window up to and including 15:00 is complete (the
# market has closed) regardless of whether more bars can arrive.
CLOSE_MINUTE_OF_DAY = 15 * 60  # 900


def _emission_cutoff_minute(last_bar_minute: int) -> int:
    """5-min rounding cutoff for window emission.

    Discards the tail window whose end minute has not been reached by a
    COMPLETE minute. Each 1-min source bar is timestamped at its START (the
    bar at 14:35 covers 14:34:00-14:35:00) and the SZSE/AkShare sources return
    real-time "close(now)", so a bar whose minute == the current wall-clock
    minute may still be in progress. Round such bars down to the previous 5-min
    window end so the in-progress tail is discarded.

    A bar from a PAST minute is already complete (the wall-clock moved past
    it), so no rounding is applied — this also correctly handles halted stocks
    whose last bar is hours old. Once the wall-clock reaches 15:00 the market
    is closed and every window up to and including 15:00 is emitted.

    Returns the latest minute-of-day whose windows are safe to emit (always
    <= last_bar_minute, so windows without data are still skipped).
    """
    now = datetime.now()
    now_minute = now.hour * 60 + now.minute
    if last_bar_minute < now_minute or now_minute >= CLOSE_MINUTE_OF_DAY:
        # Latest bar is from a past minute (complete) or market has closed.
        return last_bar_minute
    # Latest bar falls on the current wall-clock minute — may be in progress.
    # Round down to the previous 5-min window-end boundary.
    return ((last_bar_minute - 1) // 5) * 5


def _window_end_minute(bar_dt: datetime) -> time:
    """Return the end time of the 5-minute window a 1-minute bar falls into.

    Bars 09:31-09:35 -> 09:35; 09:36-09:40 -> 09:40; 13:01-13:05 -> 13:05.
    Uses the convention window = (end-5, end]; the lunch break leaves no
    stray windows because no bars exist between 11:30 and 13:01.
    """
    m = bar_dt.hour * 60 + bar_dt.minute
    end = ((m - 1) // 5 + 1) * 5
    return time(end // 60, end % 60)


def aggregate_5min(
    bare_code: str,
    name: str,
    samples: List[MinuteSample],
    emitted: set,
    trade_date,
) -> Tuple[List[dict], List[dict], Optional[time]]:
    """Aggregate 1-minute samples into 5-minute OHLCV bars for ``trade_date``.

    Samples whose date does not match ``trade_date`` are dropped — this guards
    against AkShare handing back the previous day's bars before today's session
    opens (which would otherwise make a stock look "finished" at 15:00 of the
    wrong day). Emits a window when its end time is at or before the 5-min
    rounding cutoff (``_emission_cutoff_minute``) — so the in-progress tail
    window is discarded — and it has not been emitted before.

    Args:
        emitted: mutable set of already-emitted window-end ``time`` objects
            for this stock (cleared when the loop anchors to a new biz day).
        trade_date: the biz day we are currently collecting.

    Returns (identity_rows, bar_rows, latest_time). ``latest_time`` is the
    latest in-day bar time seen (or None if no in-day samples), used to tell
    whether the stock has reached CLOSE_TIME.
    """
    if not samples:
        return [], [], None

    full_code = add_exchange_suffix(bare_code, "深圳")
    # Exchange suffix: SZ, SS, BJ, or HK are valid.
    parts = full_code.rsplit(".", 1)
    code_suffix = parts[-1] if len(parts) == 2 and parts[-1] in ("SZ", "SS", "BJ", "HK") else None

    # Keep only samples that belong to the current biz day.
    in_day: List[MinuteSample] = [s for s in samples if s[0].date() == trade_date]
    if not in_day:
        return [], [], None

    # Group samples by window-end time, preserving order within a window.
    windows: Dict[time, List[MinuteSample]] = {}
    for s in in_day:
        wend = _window_end_minute(s[0])
        windows.setdefault(wend, []).append(s)

    last_bar_minute = max(s[0].hour * 60 + s[0].minute for s in in_day)
    latest_time = time(last_bar_minute // 60, last_bar_minute % 60)

    # 5-min rounding: discard the tail window whose end minute has not been
    # reached by a COMPLETE minute (the current wall-clock minute may still
    # be in progress — sources return real-time "close(now)").
    cutoff_minute = _emission_cutoff_minute(last_bar_minute)

    identity_rows: List[dict] = []
    bar_rows: List[dict] = []
    for wend, pts in sorted(windows.items(), key=lambda kv: kv[0]):
        wend_minute = wend.hour * 60 + wend.minute
        # Skip windows whose end is past the cutoff (still in progress or
        # the source has not yet returned enough bars to complete them).
        if wend_minute > cutoff_minute:
            continue
        if wend in emitted:
            continue
        # Order points by their own timestamp within the window.
        pts.sort(key=lambda x: x[0])
        prices = [p for _, p, _ in pts]
        vols = [v for _, _, v in pts]
        o = prices[0]
        h = max(prices)
        low = min(prices)
        c = prices[-1]
        vol = sum(vols)
        change = round(c - o, 4)
        change_pct = round((c - o) / o * 100, 4) if o else None

        # is_in_index_or_etf=True by construction: load_target_stocks() pre-filters the
        # streaming target list to stocks whose latest stock_identity row (last
        # 30 days) has is_in_index_or_etf=TRUE, so every identity row emitted here is
        # for an ETF-held stock. Setting it explicitly keeps new (date, code)
        # rows from defaulting to FALSE before the next backfill runs.
        identity_rows.append({
            "date": trade_date,
            "code": full_code,
            "code_suffix": code_suffix,
            "name": name,
            "is_in_index_or_etf": True,
        })
        bar_rows.append({
            "date": trade_date,
            "code": full_code,
            "code_suffix": code_suffix,
            "time": wend,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "trading_shares": vol,
            "change": change,
            "change_pct": change_pct,
        })
        emitted.add(wend)
    return identity_rows, bar_rows, latest_time


def aggregate_index_5min(
    bare_code: str,
    name: str,
    samples: List[MinuteSample],
    emitted: set,
    trade_date,
) -> Tuple[List[dict], List[dict], Optional[time]]:
    """Aggregate 1-minute index samples into 5-minute OHLC bars (no volume).

    Mirrors ``aggregate_5min`` but emits rows for ``stats.index_intraday_5min``
    which has NO trading_shares / code_suffix columns and stores the code BARE
    (e.g. "399001", not "399001.SZ").

    Returns (identity_rows, bar_rows, latest_time).
    """
    if not samples:
        return [], [], None
    in_day: List[MinuteSample] = [s for s in samples if s[0].date() == trade_date]
    if not in_day:
        return [], [], None

    windows: Dict[time, List[MinuteSample]] = {}
    for s in in_day:
        wend = _window_end_minute(s[0])
        windows.setdefault(wend, []).append(s)

    last_bar_minute = max(s[0].hour * 60 + s[0].minute for s in in_day)
    latest_time = time(last_bar_minute // 60, last_bar_minute % 60)

    # 5-min rounding: discard the in-progress tail window (same as stocks).
    cutoff_minute = _emission_cutoff_minute(last_bar_minute)

    identity_rows: List[dict] = []
    bar_rows: List[dict] = []
    for wend, pts in sorted(windows.items(), key=lambda kv: kv[0]):
        wend_minute = wend.hour * 60 + wend.minute
        if wend_minute > cutoff_minute:
            continue
        if wend in emitted:
            continue
        pts.sort(key=lambda x: x[0])
        prices = [p for _, p, _ in pts]
        o = prices[0]
        h = max(prices)
        low = min(prices)
        c = prices[-1]
        change = round(c - o, 4)
        change_pct = round((c - o) / o * 100, 4) if o else None

        identity_rows.append({
            "date": trade_date,
            "code": bare_code,
            "name": name,
        })
        bar_rows.append({
            "date": trade_date,
            "code": bare_code,
            "time": wend,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "change": change,
            "change_pct": change_pct,
        })
        emitted.add(wend)
    return identity_rows, bar_rows, latest_time
