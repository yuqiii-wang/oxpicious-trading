"""BSE stream model — stream state, EOD-update schedule, daily bar emission.

Two data sources, both from www.bse.cn:

  * STOCKS (EOD update, the DEFAULT mode) — at ``EOD_UPDATE_TIME`` (16:00,
    when bse.cn's day data is final — the same rule the BSE index archive
    applies via SESSION_END_HOUR) each 北证50 (899050) / 北证专精特新
    (899601) constituent's per-minute time-sharing series is fetched from
    the chart behind
    ``https://www.bse.cn/products/neeq_listed_companies/company_time_sharing.html?companyCode=<code>&typename=G``
    (``companyEchartsController/getTimeSharingChart``, see
    ``fetch_bse_company_timesharing``) and folded into ONE whole-day OHLCV
    bar per stock, stamped CLOSE_TIME (15:00) to stay on the shared
    5-minute grid — open/high/low from the series' day fields, close from
    the last minute, ``trading_shares`` as the sum of per-minute volumes.
    Restricting the EOD download to the two indexes' constituents bounds
    the per-company request count (~200 codes at ~2.5s each).
  * INDICES — unchanged whole-session re-aggregation: one GET per index
    returns the entire day's per-minute series, so the EOD poll rebuilds
    every 5-min window idempotently and a restart self-heals.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from _common.labels import BOARD_CODE
from downloads._common import is_trading_day, poll_interval_sec_for, setup_logger

logger = setup_logger("stream_bse")

SERVICE = "downloads.stream.bse.price"

# EOD-update window (Asia/Shanghai). bse.cn data is final at 16:00, so the
# poller idles outside [16:00, 17:30] on trading days; a healthy day
# finishes with its first EOD sweep (~200 codes x ~2.5s + retries) and the
# rest of the window is only a retry budget for failed codes. Same 16:00
# afternoon-start rule as the szse/csindex/cnindex streamers.
EOD_UPDATE_TIME = time(16, 0)
EOD_GIVE_UP_TIME = time(17, 30)

# Stamp of the final whole-day stock bar — keeps bars on the 09:35..15:00
# grid every other streamer writes, and doubles as the finished-code marker
# (a code with a 15:00 bar today is done).
CLOSE_TIME = time(15, 0)

# A series counts as complete once its last minute reaches the final
# regular minutes; some feeds stop at the 14:57 close-auction print instead
# of a 15:00 row. Applies to BOTH legs: an index is done for the day once
# its re-aggregated series reaches here, and a stock's time-sharing series
# is accepted as final only past here (an EOD fetch that lags mid-session
# must not freeze a partial-day bar).
SESSION_COMPLETE_TIME = time(14, 55)

# Poll-loop cadence — resolved once at import from the service's
# external_request_config JSON row (request_type "poll"); the old 60s
# literal stays as the fallback.
DEFAULT_POLL_INTERVAL_SEC = poll_interval_sec_for(SERVICE, "poll") or 60

# Local CSV archive of one stock's per-minute time-sharing rows: one file
# per code per trading day under temps/bse_intraday/ — the only record of
# the per-minute volume/amount (the DB stores just the daily bar), so
# missed 5-min windows could be backfilled from CSV later. Columns keep the
# endpoint's raw units (shares / yuan).
STOCK_MINUTE_CSV_COLUMNS = [
    "update_time", "date", "code", "price", "day_open", "day_high",
    "day_low", "prev_close", "volume", "amount",
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
    """Per-day streaming state.

        target_names: bare code -> stock name (loaded once per EOD day from
            stats.sec_composition + stats.stock_identity; feeds identity
            rows).
        finished_codes: bare codes that already have today's final bar
            (kept across window retries; pre-populated from the DB so a
            post-close restart does not reprocess).
        indices_done: index codes whose re-aggregated series reached
            SESSION_COMPLETE_TIME today (their GET is skipped afterwards).
    """
    target_names: Dict[str, str] = field(default_factory=dict)
    finished_codes: set = field(default_factory=set)
    indices_done: set = field(default_factory=set)


# ---------------------------------------------------------------------------
# EOD-update schedule helpers
# ---------------------------------------------------------------------------
def in_eod_window(dt: datetime) -> bool:
    """True on a trading day inside the EOD window [16:00, 17:30] — the
    poll loop only fetches in there."""
    t = dt.time()
    return (
        is_trading_day(dt.date())
        and EOD_UPDATE_TIME <= t <= EOD_GIVE_UP_TIME
    )


def next_eod_update(now: datetime) -> datetime:
    """Next EOD-update start: today's 16:00 when a trading day is still
    before the window, otherwise the next trading day's 16:00."""
    d = now.date()
    if is_trading_day(d) and now.time() < EOD_UPDATE_TIME:
        return datetime.combine(d, EOD_UPDATE_TIME)
    nd = d + timedelta(days=1)
    while not is_trading_day(nd):
        nd += timedelta(days=1)
    return datetime.combine(nd, EOD_UPDATE_TIME)


def sleep_until(target_dt: datetime, chunk_sec: float = 60.0) -> None:
    """Sleep until target_dt, in chunks so KeyboardInterrupt stays responsive."""
    while True:
        now = datetime.now()
        if now >= target_dt:
            return
        remaining = (target_dt - now).total_seconds()
        _time.sleep(min(chunk_sec, max(0.0, remaining)))


# ---------------------------------------------------------------------------
# Final (whole-day) stock bar — from one code's per-minute time-sharing rows
# ---------------------------------------------------------------------------
def daily_bar_from_minutes(
    records: List[dict],
    trade_date,
    code: str,
    name: str,
) -> Tuple[Optional[dict], Optional[dict], Optional[time]]:
    """Fold one code's per-minute time-sharing rows into its final bar.

    Rows arrive unsorted with per-minute price (HQZJCJ) / volume (HQCJSL) /
    amount (HQCJJE) plus the day's RUNNING open/high/low (HQJRKP/HQZGCJ/
    HQZDCJ); the last-by-time row therefore carries the day's final state:
    open/high/low from the day fields, close = last minute's price, and
    ``trading_shares`` = the SUM of per-minute volumes. The bar is stamped
    CLOSE_TIME (15:00); change/change_pct keep the bar convention
    (close - open).

    Returns (identity_row, bar_row, latest_minute_time). (None, None, t)
    with t < SESSION_COMPLETE_TIME means the series is still partial — the
    caller retries later in the EOD window; (None, None, None) means no
    usable row (treated the same, e.g. suspended all day).

    Rows whose HQJSRQ (trade date) is not ``trade_date`` are dropped, so a
    stale feed cannot bleed yesterday's series into today's bar.
    """
    rows = []
    for rec in records or []:
        gxsj = str(rec.get("HQGXSJ") or "").strip()
        jsrq = str(rec.get("HQJSRQ") or "").strip()
        if len(gxsj) < 4:
            continue
        if jsrq and trade_date is not None:
            row_date = f"{trade_date:%Y%m%d}"
            if jsrq != row_date:
                continue
        rows.append(rec)
    if not rows:
        return None, None, None

    rows.sort(key=lambda r: str(r.get("HQGXSJ") or ""))
    last = rows[-1]
    latest_t = time(int(str(last["HQGXSJ"])[:2]), int(str(last["HQGXSJ"])[2:4]))

    def _num(val) -> Optional[float]:
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    close = _num(last.get("HQZJCJ"))
    if close is None:
        return None, None, latest_t

    opens = [v for v in (_num(r.get("HQJRKP")) for r in rows) if v is not None]
    highs = [v for v in (_num(r.get("HQZGCJ")) for r in rows) if v is not None]
    lows = [v for v in (_num(r.get("HQZDCJ")) for r in rows) if v is not None]
    vols = [v for v in (_num(r.get("HQCJSL")) for r in rows) if v is not None]
    o = opens[-1] if opens else None
    h = max(highs) if highs else None
    low = min(lows) if lows else None
    vol = sum(vols) if vols else None

    if latest_t < SESSION_COMPLETE_TIME:
        # Session still in progress (or feed lagging) — do not freeze a
        # partial-day bar; the caller retries later in the window.
        return None, None, latest_t

    full_code = f"{code}.BJ"
    identity_row = {
        "date": trade_date,
        "code": full_code,
        "exchange": "BJ",
        "board": BOARD_CODE["BSE"],
        "name": name,
        "is_in_index_or_etf": True,
    }
    bar_row = {
        "date": trade_date,
        "code": full_code,
        "exchange": "BJ",
        "time": CLOSE_TIME,
        "open": o,
        "high": h,
        "low": low,
        "close": close,
        "trading_shares": vol,
        "change": round(close - o, 4) if o else None,
        "change_pct": round((close - o) / o * 100, 4) if o else None,
    }
    return identity_row, bar_row, latest_t


# ---------------------------------------------------------------------------
# Index (北证50) 5-minute aggregation — from per-minute realtime rows
# ---------------------------------------------------------------------------
def aggregate_index_bars(
    records: List[dict],
    trade_date,
    code: str = "899050",
    name: str = "北证50",
) -> Tuple[List[dict], List[dict], Optional[time]]:
    """Aggregate getRealBZ50/getRealZJTX per-minute records into 5-min OHLC
    bars for one BSE index.

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
