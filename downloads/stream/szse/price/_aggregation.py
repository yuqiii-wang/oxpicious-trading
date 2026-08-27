"""SZSE 5-minute OHLCV aggregation — stock and index bar builders.

Aggregates 1-minute samples (from any of the four API sources) into 5-minute
OHLCV bars. Stock bars include ``trading_shares`` and ``exchange``;
index bars omit both (matching the ``index_intraday_5min`` schema).
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Dict, List, Optional, Tuple

from downloads._common import add_exchange_suffix

from ._akshare_source import MinuteSample

# 15:00 (market close) expressed as minutes-of-day. Once the wall-clock
# reaches this, every window up to and including 15:00 is complete (the
# market has closed) regardless of whether more bars can arrive.
CLOSE_MINUTE_OF_DAY = 15 * 60  # 900


def _emission_cutoff_minute(last_bar_minute: int, data_date=None) -> int:
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

    If ``data_date`` is from a previous day (data_date < local today), the
    market has definitely closed for that data's day — emit all windows up to
    the last bar without rounding (avoids incorrectly discarding the 15:00
    window when comparing minute-of-day values across different dates).

    Returns the latest minute-of-day whose windows are safe to emit (always
    <= last_bar_minute, so windows without data are still skipped).
    """
    now = datetime.now()
    # Data from a previous day is fully closed — no in-progress tail to discard.
    if data_date is not None and data_date < now.date():
        return last_bar_minute
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
    """Aggregate 1-minute samples into 5-minute OHLCV bars.

    The trade_date used for row stamps and sample filtering is derived from
    the SAMPLES' own datetimes (the remote source's logged date — e.g. SZSE
    ``marketTime``, AkShare ``day``, EM ``time_str``), NOT from the local
    request time. The passed-in ``trade_date`` is only used to detect a
    date mismatch: when the remote date differs from the local biz day,
    bars are still produced (stamped with the remote date) but
    ``latest_time`` is returned as None so the stock is NOT marked as
    finished — it will be re-fetched until today's data arrives.

    Sources like EM push2his return up to 5 days of data; only the most
    recent trading day's samples are kept (``max(sample dates)``).

    Emits a window when its end time is at or before the 5-min rounding
    cutoff (``_emission_cutoff_minute``) and it has not been emitted before.
    The ``emitted`` set is keyed by ``(date, time)`` so windows from different
    dates don't collide.

    Returns (identity_rows, bar_rows, latest_time). ``latest_time`` is the
    latest in-day bar time seen (or None if no in-day samples or date
    mismatch), used to tell whether the stock has reached CLOSE_TIME.
    """
    if not samples:
        return [], [], None

    full_code = add_exchange_suffix(bare_code, "深圳")
    # Canonical exchange (SZ for SZSE streams).
    exchange = "SZ" if full_code.endswith(".SZ") else None

    # Derive trade_date from the samples' own dates (remote source logged
    # date), NOT from the local request time. Use the latest date so multi-day
    # sources (EM push2his ndays=5) only emit the most recent trading day.
    derived_date = max(s[0].date() for s in samples)

    # Keep only samples that belong to the derived (remote) date.
    in_day: List[MinuteSample] = [s for s in samples if s[0].date() == derived_date]
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
    cutoff_minute = _emission_cutoff_minute(last_bar_minute, derived_date)

    identity_rows: List[dict] = []
    bar_rows: List[dict] = []
    for wend, pts in sorted(windows.items(), key=lambda kv: kv[0]):
        wend_minute = wend.hour * 60 + wend.minute
        # Skip windows whose end is past the cutoff (still in progress or
        # the source has not yet returned enough bars to complete them).
        if wend_minute > cutoff_minute:
            continue
        emit_key = (derived_date, wend)
        if emit_key in emitted:
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
            "date": derived_date,
            "code": full_code,
            "exchange": exchange,
            "name": name,
            "is_in_index_or_etf": True,
        })
        bar_rows.append({
            "date": derived_date,
            "code": full_code,
            "exchange": exchange,
            "time": wend,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "trading_shares": vol,
            "change": change,
            "change_pct": change_pct,
        })
        emitted.add(emit_key)

    # If the remote-derived date doesn't match the local biz day, return
    # latest_time=None so the stock is NOT marked as finished. The bars are
    # still upserted (stamped with the correct remote date), but the stock
    # will be re-fetched until today's data arrives.
    if derived_date != trade_date:
        return identity_rows, bar_rows, None
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
    which has NO trading_shares / exchange columns and stores the code BARE
    (e.g. "399001", not "399001.SZ").

    The trade_date is derived from the samples' own datetimes (remote source
    logged date, e.g. SZSE ``marketTime``), NOT from the local request time.
    See ``aggregate_5min`` for full semantics.

    Returns (identity_rows, bar_rows, latest_time).
    """
    if not samples:
        return [], [], None

    # Derive trade_date from the samples' own dates (remote source logged
    # date), NOT from the local request time.
    derived_date = max(s[0].date() for s in samples)
    in_day: List[MinuteSample] = [s for s in samples if s[0].date() == derived_date]
    if not in_day:
        return [], [], None

    windows: Dict[time, List[MinuteSample]] = {}
    for s in in_day:
        wend = _window_end_minute(s[0])
        windows.setdefault(wend, []).append(s)

    last_bar_minute = max(s[0].hour * 60 + s[0].minute for s in in_day)
    latest_time = time(last_bar_minute // 60, last_bar_minute % 60)

    # 5-min rounding: discard the in-progress tail window (same as stocks).
    cutoff_minute = _emission_cutoff_minute(last_bar_minute, derived_date)

    identity_rows: List[dict] = []
    bar_rows: List[dict] = []
    for wend, pts in sorted(windows.items(), key=lambda kv: kv[0]):
        wend_minute = wend.hour * 60 + wend.minute
        if wend_minute > cutoff_minute:
            continue
        emit_key = (derived_date, wend)
        if emit_key in emitted:
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
            "date": derived_date,
            "code": bare_code,
            "name": name,
        })
        bar_rows.append({
            "date": derived_date,
            "code": bare_code,
            "time": wend,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "change": change,
            "change_pct": change_pct,
        })
        emitted.add(emit_key)

    # If the remote-derived date doesn't match the local biz day, return
    # latest_time=None (same rationale as aggregate_5min).
    if derived_date != trade_date:
        return identity_rows, bar_rows, None
    return identity_rows, bar_rows, latest_time
