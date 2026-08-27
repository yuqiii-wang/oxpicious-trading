"""SZSE stream workers — async fetch dispatch, circuit breakers, index rounds.

Contains the async machinery that drives the four parallel source workers
(A=akshare, C=em_push2his, D=em_push2, B=szse fallback) plus the index round
runner. Also holds biz-day helpers (wait_for_next_trading_day, sleep_chunks,
_seconds_until_next_hour), split_groups, and the per-stock circuit-breaker
logic that hands failed groups from A/C/D to the szse fallback worker.
"""
from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from downloads._common import (
    HostStatusTracker,
    add_exchange_suffix,
    is_trading_day,
    random_sleep,
    setup_logger,
)

from _common.db_commons import check_stock_intraday_exists

from ._aggregation import aggregate_5min, aggregate_index_5min
from ._akshare_source import MinuteSample, fetch_akshare_minute
from ._em_source import fetch_em_push2_minute, fetch_em_push2his_minute
from ._io import load_bars_sync, load_index_bars_sync
from ._szse_source import fetch_szse_index_minute, fetch_szse_minute

logger = setup_logger("stream_szse")

# A stock is "finished" for its biz day once its latest 1-minute bar reaches
# the market close (15:00). Rounds keep re-fetching unfinished stocks until
# they all reach CLOSE_TIME, then the loop waits for the next trading day.
CLOSE_TIME = time(15, 0)

# Per-fetch hard timeout (seconds). If a single AkShare / SZSE fetch takes
# longer than this we abandon it (daemon thread winds down in the background)
# and log a timeout — so a hung fetch never stalls the whole stream.
FETCH_TIMEOUT_SEC = 120.0

# Emit a progress line every N stocks processed.
PROGRESS_EVERY = 25


# ---------------------------------------------------------------------------
# Biz-day helpers
# ---------------------------------------------------------------------------
def _next_trading_day_after(d) -> "date":
    """First trading day strictly after ``d``."""
    nd = d + timedelta(days=1)
    while not is_trading_day(nd):
        nd += timedelta(days=1)
    return nd


def wait_for_next_trading_day(current_biz_day, chunk_sec: float = 5.0) -> "date":
    """Sleep (in chunks, cancellation-responsive) until a new trading day.

    Returns the new trading day's date. Used after all stocks of the current
    biz day have reached CLOSE_TIME, before anchoring to the next biz day.
    """
    nxt = _next_trading_day_after(current_biz_day)
    while datetime.now().date() < nxt:
        _time.sleep(chunk_sec)
    return nxt


def sleep_chunks(sec: float, chunk_sec: float = 5.0) -> None:
    """Sleep for ``sec`` in chunks so Ctrl-C stays responsive (sync)."""
    end = _time.time() + max(0.0, sec)
    while _time.time() < end:
        _time.sleep(min(chunk_sec, max(0.0, end - _time.time())))


async def async_sleep_chunks(sec: float, chunk_sec: float = 5.0) -> None:
    """Async sleep for ``sec`` in chunks. Responds to asyncio cancellation.

    Unlike ``asyncio.to_thread(sleep_chunks, ...)``, this raises
    ``CancelledError`` immediately when the task is cancelled — no thread
    to wait for.
    """
    end = _time.time() + max(0.0, sec)
    while _time.time() < end:
        remaining = end - _time.time()
        await asyncio.sleep(min(chunk_sec, max(0.0, remaining)))


def _seconds_until_next_hour() -> float:
    """Seconds from now until the next HH:00:00 boundary (at least 1s)."""
    now = datetime.now()
    # Next hour: e.g. 10:35 -> 11:00
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return max(1.0, (next_hour - now).total_seconds())


# A-share trading sessions (Asia/Shanghai): 09:30-11:30, 13:00-15:00.
TRADING_SESSIONS: List[Tuple[time, time]] = [
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
]


def next_trading_moment(now: datetime) -> datetime:
    """Return the next datetime at which trading is active (today or next trading day).

    Mirrors downloads.stream.sse.price._model.next_trading_moment: walks the
    A-share session boundaries (09:30 open, 11:30-13:00 lunch, 15:00 close)
    and falls through to the next trading day's 09:30 after close. Used by
    hourly mode to wait for the next trading session once all stocks have
    finished for the day instead of sleeping one hour.
    """
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
    """Sleep until target_dt, in chunks so KeyboardInterrupt stays responsive (sync)."""
    while True:
        now = datetime.now()
        if now >= target_dt:
            return
        remaining = (target_dt - now).total_seconds()
        _time.sleep(min(chunk_sec, max(0.0, remaining)))


async def async_sleep_until(target_dt: datetime, chunk_sec: float = 60.0) -> None:
    """Async sleep until target_dt. Responds to asyncio cancellation.

    Unlike ``asyncio.to_thread(sleep_until, ...)``, this raises
    ``CancelledError`` immediately when the task is cancelled — critical
    because ``sleep_until`` may be called with a target_dt hours away
    (e.g. next trading day 09:30), and threads can't be killed in Python.
    """
    while True:
        now = datetime.now()
        if now >= target_dt:
            return
        remaining = (target_dt - now).total_seconds()
        await asyncio.sleep(min(chunk_sec, max(0.0, remaining)))


async def async_random_sleep(base_sec: float, jitter_factor: float = 0.5) -> None:
    """Async random_sleep. Responds to asyncio cancellation.

    Mirrors ``random_sleep`` from ``downloads._common.net`` but uses
    ``asyncio.sleep``
    so the task can be cancelled instantly — no thread to wait for on
    shutdown.
    """
    if base_sec <= 0:
        return
    import random as _r
    jitter = base_sec * jitter_factor
    sleep_time = _r.uniform(base_sec - jitter, base_sec + jitter)
    await asyncio.sleep(max(0.0, sleep_time))


async def probe_szse_for_today_bars(
    session,
    host_tracker,
    target_date,
    probe_code: str,
) -> Tuple[bool, int]:
    """Probe the SZSE source by fetching one index. Returns (has_today_bars, n_today_samples).

    Used on fresh start to detect whether the SZSE API has intraday data for
    ``target_date``. If the probe returns zero bars (or only yesterday's
    cached data), the caller should wait until trading hours start before
    entering the main loop — this prevents wasting API calls fetching zero
    bars pre-market or during lunch break.

    The probe fetches an always-on SZSE index via the same ssjjhq endpoint
    used by the index round, and checks that the returned samples are dated
    for ``target_date`` (the API may return stale data outside trading hours).
    """
    samples = await asyncio.to_thread(
        fetch_szse_index_minute, session, probe_code, host_tracker
    )
    if not samples:
        return False, 0
    today_samples = [s for s in samples if s[0].date() == target_date]
    return bool(today_samples), len(today_samples)


def split_groups(stocks: List[Tuple[str, str]], n: int) -> List[List[Tuple[str, str]]]:
    """Split stocks into ``n`` near-equal groups (round-robin for balance).

    Kept for compatibility / callers, though the main loop no longer runs
    groups concurrently — it streams stocks one at a time on a single thread.
    """
    groups: List[List[Tuple[str, str]]] = [[] for _ in range(n)]
    for i, item in enumerate(stocks):
        groups[i % n].append(item)
    return [g for g in groups if g]


def _not_finished(lt: Optional[time]) -> bool:
    """A stock is unfinished until its latest in-day bar reaches CLOSE_TIME."""
    return lt is None or lt < CLOSE_TIME


# ---------------------------------------------------------------------------
# Fetch dispatch: routes one stock to the given source's fetch function
# ---------------------------------------------------------------------------
async def _fetch_async(source: str, bare_code: str, session, host_tracker):
    """Fetch one stock's minute samples via the given source.

    Runs the blocking fetch in a worker thread (so the event loop stays free
    for the other async worker) with a hard FETCH_TIMEOUT_SEC timeout. Returns
    the samples list, or None on failure / timeout (the "no data downloaded
    for 2 min → timeout" notice fires here).
    """
    if source == "akshare":
        coro = asyncio.to_thread(fetch_akshare_minute, bare_code)
    elif source == "em_push2his":
        coro = asyncio.to_thread(fetch_em_push2his_minute, bare_code)
    elif source == "em_push2":
        coro = asyncio.to_thread(fetch_em_push2_minute, bare_code)
    else:
        coro = asyncio.to_thread(fetch_szse_minute, session, bare_code, host_tracker)
    try:
        return await asyncio.wait_for(coro, timeout=FETCH_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        logger.warning("[timeout] %s %s exceeded %.0fs — no data downloaded; skipping.",
                       source, bare_code, FETCH_TIMEOUT_SEC)
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s %s] raised: %s", source, bare_code, e)
        return None


# ---------------------------------------------------------------------------
# Group processing (synchronous, one stock at a time): AkShare primary,
# SZSE fallback on 4xx / failure
# ---------------------------------------------------------------------------
async def _process_stocks(
    source: str,
    stocks: List[Tuple[str, str]],
    session,
    host_tracker: HostStatusTracker,
    emitted_map: Dict[str, set],
    trade_date,
    cooldown_sec: float,
    tag: str = "",
    conn=None,
) -> Tuple[List[dict], List[dict], Dict[str, Optional[time]], List[Tuple[str, str]]]:
    """Process a list of stocks with ONE source, async.

    One request per stock, with the anti-bot cooldown (random_sleep) between
    fetches (run in a thread so the event loop stays free for the sibling
    worker). Per-stock INFO logging shows data flowing from each source.

    Circuit breaker: each source has a ``max_consecutive_failures`` threshold.
    When that many consecutive failures occur, the worker returns the remainder
    (from the current failed stock onward) so the szse worker can resume it.
      * akshare (A): 1  — stop immediately on first failure (V8 crash risk).
      * em_push2his/push2 (C/D): 3  — ride through 1-2 transient failures
        before giving up (EM endpoints can be flaky but not uniformly dead).
      * szse (B): 0  — never stops (last resort, always continues).
    Returns (identity_rows, bar_rows, {code: latest_time}, remainder).
    """
    if source == "akshare":
        max_consecutive_failures = 1
    elif source in ("em_push2his", "em_push2"):
        max_consecutive_failures = 3
    else:  # szse — last resort, never give up
        max_consecutive_failures = 0
    consecutive_failures = 0

    identity_rows: List[dict] = []
    bar_rows: List[dict] = []
    latest_times: Dict[str, Optional[time]] = {}
    n_stocks = len(stocks)
    t_start = _time.time()

    for i, (bare_code, name) in enumerate(stocks):
        ft0 = _time.time()
        samples = await _fetch_async(source, bare_code, session, host_tracker)
        fe = _time.time() - ft0
        if samples is not None:
            consecutive_failures = 0  # reset on success
            emitted = emitted_map.setdefault(bare_code, set())
            ident, bars, lt = aggregate_5min(bare_code, name, samples, emitted, trade_date)
            latest_times[bare_code] = lt
            identity_rows.extend(ident)
            bar_rows.extend(bars)

            # Check if we got samples but 0 bars (likely date mismatch)
            if len(bars) == 0 and len(samples) > 0:
                # Check if data already exists for this stock on trade_date
                data_existed = False
                if conn is not None:
                    full_code = add_exchange_suffix(bare_code, "深圳")
                    data_existed = await asyncio.to_thread(
                        check_stock_intraday_exists, conn, full_code, trade_date
                    )

                if data_existed:
                    logger.info(
                        "%s[%s] %s: %d samples -> 0 bars, data existed for %s, skipped",
                        tag, source, bare_code, len(samples), trade_date,
                    )
                else:
                    logger.info(
                        "%s[%s] %s: %d samples -> 0 bars (latest=%s) in %.1fs",
                        tag, source, bare_code, len(samples), lt, fe,
                    )
            else:
                logger.info(
                    "%s[%s] %s: %d samples -> %d bars (latest=%s) in %.1fs",
                    tag, source, bare_code, len(samples), len(bars), lt, fe,
                )
            # Incremental DB upsert so each stock's bars appear in
            # stats.stock_intraday_5min immediately (not only at round-end).
            if conn is not None and (ident or bars):
                up_t0 = _time.time()
                try:
                    await asyncio.to_thread(load_bars_sync, conn, ident, bars)
                    logger.info(
                        "%s[%s] %s: upserted %d identity + %d bars in %.2fs",
                        tag, source, bare_code, len(ident), len(bars),
                        _time.time() - up_t0,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "%s[%s] %s: DB upsert failed: %s", tag, source, bare_code, e,
                    )
        else:
            latest_times[bare_code] = None
            consecutive_failures += 1
            logger.info("%s[%s] %s: NO DATA in %.1fs (consecutive_failures=%d/%d)",
                        tag, source, bare_code, fe, consecutive_failures,
                        max_consecutive_failures if max_consecutive_failures > 0 else -1)
            if max_consecutive_failures > 0 and consecutive_failures >= max_consecutive_failures:
                # Circuit breaker tripped: hand the rest of this group back so
                # the szse worker can resume it.
                remainder = stocks[i:]
                logger.info(
                    "%s[%s] circuit breaker tripped after %d consecutive failures on %s (%d/%d); "
                    "returning %d remaining stocks for szse resume",
                    tag, source, consecutive_failures, bare_code, i + 1, n_stocks, len(remainder),
                )
                return identity_rows, bar_rows, latest_times, remainder

        if (i + 1) % PROGRESS_EVERY == 0 or (i + 1) == n_stocks:
            logger.info(
                "%s[%s] progress: %d/%d stocks in %.1fs, bars so far=%d",
                tag, source, i + 1, n_stocks, _time.time() - t_start, len(bar_rows),
            )

        # Anti-bot cooldown between fetches (skip after the last stock).
        if i < n_stocks - 1:
            await async_random_sleep(cooldown_sec)

    return identity_rows, bar_rows, latest_times, []


# ---------------------------------------------------------------------------
# Index round: fetch 3 always-on SZSE indices sequentially
# ---------------------------------------------------------------------------
async def _run_index_round(
    index_codes: List[str],
    session,
    host_tracker: HostStatusTracker,
    emitted_map_idx: Dict[str, set],
    trade_date,
    cooldown_sec: float,
    conn_idx,
) -> Tuple[int, int]:
    """Fetch a list of SZSE indices via the SZSE ssjjhq API, aggregate into
    5-min bars, and upsert into stats.index_intraday_5min.

    Runs sequentially (3 indices) with the anti-bot cooldown between fetches.
    Returns (n_identity, n_bars).
    """
    n_ident_total = 0
    n_bars_total = 0
    for i, bare_code in enumerate(index_codes):
        ft0 = _time.time()
        samples = await asyncio.to_thread(
            fetch_szse_index_minute, session, bare_code, host_tracker
        )
        fe = _time.time() - ft0
        if samples is not None:
            emitted = emitted_map_idx.setdefault(bare_code, set())
            ident, bars, lt = aggregate_index_5min(
                bare_code, bare_code, samples, emitted, trade_date
            )
            if conn_idx is not None and (ident or bars):
                up_t0 = _time.time()
                try:
                    await asyncio.to_thread(load_index_bars_sync, conn_idx, ident, bars)
                    n_ident_total += len(ident)
                    n_bars_total += len(bars)
                    logger.info(
                        "[IDX] %s: %d samples -> %d bars (latest=%s) in %.1fs; upserted %d ident + %d bars in %.2fs",
                        bare_code, len(samples), len(bars), lt, fe,
                        len(ident), len(bars), _time.time() - up_t0,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error("[IDX] %s: DB upsert failed: %s", bare_code, e)
            else:
                logger.info(
                    "[IDX] %s: %d samples -> %d bars (latest=%s) in %.1fs (no DB)",
                    bare_code, len(samples), len(bars), lt, fe,
                )
        else:
            logger.info("[IDX] %s: NO DATA in %.1fs", bare_code, fe)

        # Anti-bot cooldown between index fetches (skip after the last).
        if i < len(index_codes) - 1:
            await async_random_sleep(cooldown_sec)
    return n_ident_total, n_bars_total


# ---------------------------------------------------------------------------
# Async workers: three primary procs (A=akshare, C=em_push2his, D=em_push2)
# pull groups from a shared primary queue in parallel; one fallback proc
# (B=szse) drains the fallback queue first, then the primary. Whoever finishes
# first takes the next group. On an A/C/D failure the unfinished remainder is
# handed to the SZSE worker. V8 is only ever touched by the akshare worker, so
# the partition_address_space.cc race cannot happen.
# ---------------------------------------------------------------------------
async def _parallel_source_worker(source: str, tag: str, q_primary, q_fallback,
                                  state, session, host_tracker, emitted_map,
                                  trade_date, cooldown_sec, conn):
    """Generic primary-queue worker for sources A (akshare), C (em_push2his),
    D (em_push2). Pulls groups from q_primary; on failure hands the remainder
    to q_fallback for the szse worker to resume.
    """
    while True:
        if state["stop"]:
            return
        try:
            stocks = await asyncio.wait_for(q_primary.get(), timeout=0.5)
        except asyncio.TimeoutError:
            if state["n_outstanding"] <= 0 and q_fallback.empty():
                return
            continue
        if stocks is None:  # sentinel
            q_primary.task_done()
            return
        try:
            ident, bars, latests, remainder = await _process_stocks(
                source, stocks, session, host_tracker, emitted_map,
                trade_date, cooldown_sec, tag=tag, conn=conn,
            )
            state["identity"].extend(ident)
            state["bars"].extend(bars)
            _merge_latests(state["latest_bar_time"], latests)
            if remainder:
                # Hand the unfinished portion to the szse worker (fallback).
                await q_fallback.put(remainder)
                logger.info("%s: handed off %d stocks to szse worker.", tag, len(remainder))
            else:
                state["n_outstanding"] -= 1
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001  (work preservation)
            logger.exception("%s: error on a group (%s); requeuing for szse resume.", tag, e)
            await q_fallback.put(stocks)
        finally:
            try:
                q_primary.task_done()
            except ValueError:
                pass


async def _szse_worker(q_primary, q_fallback, state, session, host_tracker,
                       emitted_map, trade_date, cooldown_sec, conn):
    tag = "B"
    while True:
        if state["stop"]:
            return
        stocks = None
        from_fallback = True
        try:
            stocks = q_fallback.get_nowait()
        except asyncio.QueueEmpty:
            from_fallback = False
            try:
                stocks = await asyncio.wait_for(q_primary.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if state["n_outstanding"] <= 0 and q_fallback.empty():
                    return
                continue
        if stocks is None:  # sentinel
            if not from_fallback:
                q_primary.task_done()
            return
        try:
            ident, bars, latests, _rem = await _process_stocks(
                "szse", stocks, session, host_tracker, emitted_map,
                trade_date, cooldown_sec, tag=tag, conn=conn,
            )
            state["identity"].extend(ident)
            state["bars"].extend(bars)
            _merge_latests(state["latest_bar_time"], latests)
            state["n_outstanding"] -= 1
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001  (work preservation)
            logger.exception("%s: error on a group (%s); giving up on it.", tag, e)
            state["n_outstanding"] -= 1
        finally:
            if from_fallback:
                try:
                    q_fallback.task_done()
                except ValueError:
                    pass
            else:
                try:
                    q_primary.task_done()
                except ValueError:
                    pass


def _merge_latests(shared: Dict[str, Optional[time]], new: Dict[str, Optional[time]]):
    """Forward-only merge of per-stock latest bar times."""
    for code, lt in new.items():
        if lt is None:
            if shared.get(code) is None:
                shared[code] = None
            continue
        prev = shared.get(code)
        if prev is None or lt > prev:
            shared[code] = lt
