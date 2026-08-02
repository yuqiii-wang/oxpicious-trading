"""stream_sse_price.py — Stream SSE prices and build 5-minute OHLCV bars.

Polls the JSONP endpoint that powers the "刷新" (refresh) button on
https://www.sse.com.cn/market/price/report/ every 60 seconds during trading
hours, collecting the latest price (``last``) and cumulative day volume
(``volume``) for every Shanghai-listed security.

The 报表 page exposes a 类型 selector with three tabs — 股票 / 基金 / 指数 —
which map to three list endpoints sharing the same JSONP schema (only the
path suffix differs): ``exchange/equity``, ``exchange/fund``, ``exchange/index``.
This service streams 股票 and 指数 (基金 is deferred until a fund intraday
table exists):

  * 股票 (equity)  → stats.stock_intraday_5min
    - code stored WITH exchange suffix (e.g. "600000.SS")
    - per-bar volume derived by subtracting cumulative volumes across the
      5 one-minute samples (the endpoint returns today's cumulative volume)
  * 指数 (index)   → stats.index_intraday_5min  (replaces the former CSIndex
    tick-resampling path in build_csindex.py)
    - code stored BARE (e.g. "000001"), matching index_identity's CHECK
      constraint ^(\\d{6}|H\\d{5})$
    - NO volume column (index_intraday_5min has no volume field)
    - filtered to indices that already exist in stats.index_identity, so
      only tracked indices are streamed (SSE publishes ~200 indices; we
      only care about the subset we already have daily history for)

Every 5 one-minute samples are aggregated into one 5-minute OHLCV bar per
security and upserted into the corresponding intraday table:
  * open / high / low / close  ← the 5 ``last`` prices ("collect latest per min")
  * volume (stocks only)       ← last.cumvol - prev_bar.cumvol  (subtraction,
                                  because the endpoint returns today's
                                  cumulative volume, not per-bar volume)
  * change / change_pct        ← close - open, (close - open) / open * 100

Trading hours (Asia/Shanghai): 09:30-11:30, 13:00-15:00 on trading days only.
Outside trading hours the loop sleeps until the next session.

Skip logic: Once a security's bar reaches 15:00 (CLOSE_TIME), it is marked as
finished and skipped in subsequent cycles for that trade_date. This prevents
re-processing after the market closes. At startup, the script queries the
intraday tables to pre-populate finished_codes with securities that already
have a 15:00 bar for today — preventing re-processing if the script restarts
after close.

Requires tables from database/sql/stats/05_index_baseline.sql
(index_identity + index_intraday_5min) and 06_stock_baseline.sql
(stock_identity + stock_intraday_5min). Run those SQL first.

Usage:
  python stream_sse_price.py                  # stream all day (60s poll)
  python stream_sse_price.py --interval 10    # dev: 10s poll interval
  python stream_sse_price.py --once           # emit one 5-sample bar then exit
  python stream_sse_price.py --bar-window 3   # dev: 3-sample bars
  python stream_sse_price.py --no-index       # stream stocks only
"""
from __future__ import annotations

import argparse
import csv
import locale as _locale
import sys
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from downloads._common.core import (
    DEFAULT_TIMEOUT,
    HostStatusTracker,
    add_exchange_suffix,
    build_default_session,
    is_trading_day,
    resolve_out_dir,
    setup_logger,
)
from utils.db_commons import (
    bulk_upsert,
    get_db_connection,
)
from utils.study_and_select_stocks import (
    ETF_WEIGHT_THRESHOLD,
    load_etf_member_codes,
)
# Reuse the SSE JSONP fetch/parse helpers + shared list-endpoint constants
# from download_sse_trend (the today/snapshot half of the former
# download_sse_price). _fetch_page and _extract_update_datetime are defined
# locally because the streaming loop uses a different fetch signature
# (host_tracker instead of AntiBotProxy) and needs the full real-time field
# set, not just code+name.
from downloads.stock.sse._common.list_endpoint import (
    INTER_PAGE_SLEEP_SEC,
    JSONP_CALLBACK,
    PAGE_SIZE,
    SSE_HEADERS,
    SSE_LIST_URL,
    STREAM_SELECT_FIELDS,
    _num,
    _parse_jsonp,
)

# ---------------------------------------------------------------------------
# stdout encoding (Windows)
# ---------------------------------------------------------------------------
try:
    _locale.setlocale(_locale.LC_ALL, "")
except Exception:
    pass
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


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

# SSE list endpoints by asset type. The 股票/基金/指数 tabs on
# https://www.sse.com.cn/market/price/report/ share the same JSONP list API;
# only the path suffix differs (equity/fund/index). 基金 is deferred until a
# fund intraday table exists, so only equity + index are streamed today.
SSE_INDEX_LIST_URL = SSE_LIST_URL.replace("/equity", "/index")

# Local CSV archive: one file per trading day, appended to on every poll,
# under temps/<subdir>/.
CSV_COLUMNS = [
    "update_time", "code", "name", "open", "high", "low", "last",
    "prev_close", "change", "volume", "amount",
]

# STREAM_SELECT_FIELDS and INTER_PAGE_SLEEP_SEC are imported from
# download_sse_trend (shared with the snapshot fetcher). The streaming
# endpoint needs last/volume/open/... — unlike the archive's list fetcher
# which only selects code+name and fetches OHLCV via the dayk endpoint.


@dataclass
class AssetStream:
    """Per-asset-type streaming context (股票 / 指数).

    Encapsulates everything that differs between the equity and index tabs of
    the SSE report page: the list endpoint, target tables, code format, whether
    per-bar volume is recorded, the optional "only load existing codes" filter,
    and the mutable streaming state (sample buffer, cumulative-volume
    baseline, finished-codes set).

    Attributes:
        name: 'stock' or 'index'.
        list_url: SSE list endpoint for this type.
        identity_table: FK parent table (e.g. stats.stock_identity).
        intraday_table: bar table (e.g. stats.stock_intraday_5min).
        code_suffix: exchange suffix appended to codes ('SS' for stocks), or
            None for indices (bare 6-digit code per index_identity CHECK).
        has_volume: stocks record per-bar volume; indices do not
            (index_intraday_5min has no volume column).
        allowed_codes: optional allow-list of bare codes. When set (indices),
            snapshot rows whose code is NOT in this set are dropped before
            entering the buffer — implements "only load to existing index".
        csv_subdir: subdirectory under temps/ for the daily CSV archive.
        csv_prefix: filename prefix for the daily CSV archive.
        buffer: list of (update_dt, snapshot) samples collected this bar.
        prev_bar_cumvol: bare_code -> cumulative volume at the end of the
            previous bar. Stocks only (indices have no volume).
        finished_codes: bare codes that already reached CLOSE_TIME today.
    """
    name: str
    list_url: str
    identity_table: str
    intraday_table: str
    code_suffix: Optional[str]
    has_volume: bool
    allowed_codes: Optional[set] = None
    csv_subdir: str = "sse_intraday"
    csv_prefix: str = "sse_intraday"
    buffer: List[Tuple[datetime, "Snapshot"]] = field(default_factory=list)
    prev_bar_cumvol: Dict[str, float] = field(default_factory=dict)
    finished_codes: set = field(default_factory=set)


# ---------------------------------------------------------------------------
# SSE list endpoint helpers (local — signature differs from download_sse_trend)
# ---------------------------------------------------------------------------
def _extract_update_datetime(payload: Dict[str, Any]) -> Optional[datetime]:
    """Extract the snapshot update datetime from the SSE list endpoint response.

    The yunhq list endpoint returns top-level ``date`` (YYYYMMDD) and ``time``
    (HHMMSS) fields — the "更新时间" shown on the webpage, not the local clock.
    Returns None if the fields are missing or unparseable.
    """
    date_raw = payload.get("date")
    time_raw = payload.get("time")
    if not date_raw:
        return None
    try:
        date_str = str(date_raw)
        time_str = str(time_raw).zfill(6) if time_raw is not None else "000000"
        dt_str = (
            f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} "
            f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
        )
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, IndexError) as e:
        logger.warning(
            "Failed to parse SSE update time: date=%r time=%r error=%s",
            date_raw, time_raw, e,
        )
        return None


def _fetch_page(
    session: requests.Session,
    begin: int,
    end: int,
    list_url: str = SSE_LIST_URL,
    host_tracker: Optional[HostStatusTracker] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch one page from an SSE list endpoint with full real-time fields.

    Unlike ``download_sse_trend._fetch_snapshot_page`` this returns the raw
    payload (caller drives pagination + update-datetime extraction) so the
    60-second polling cadence is not slowed by the 20s anti-bot sleep.
    ``list_url`` selects the asset type (equity/fund/index); ``host_tracker``
    records 4xx errors for blocking detection.
    """
    params = {
        "callback": JSONP_CALLBACK,
        "begin": str(begin),
        "end": str(end),
        "select": STREAM_SELECT_FIELDS,
    }
    try:
        resp = session.get(
            list_url,
            params=params,
            headers=SSE_HEADERS,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("SSE list page %d-%d request failed: %s", begin, end, e)
        return None

    if resp.status_code != 200 and host_tracker is not None:
        host_tracker.record_error(list_url, resp.status_code, resp.reason)

    try:
        return _parse_jsonp(resp.text)
    except ValueError as e:
        logger.warning("SSE list page %d-%d JSONP parse failed: %s", begin, end, e)
        return None


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
# Snapshot fetching (paginates the SSE list endpoint exactly like the webpage)
# ---------------------------------------------------------------------------
def _parse_snapshot_row(row: list) -> Optional[Dict[str, object]]:
    """Map one SSE list row to a full record dict.

    Row order matches STREAM_SELECT_FIELDS in download_sse_trend:
    code, name, open, high, low, last, prev_close, change, volume, amount
    """
    if not row:
        return None
    code = str(row[0]).strip() if row[0] is not None else ""
    if not code:
        return None
    return {
        "code": code,
        "name": str(row[1]).strip() if len(row) > 1 and row[1] is not None else "",
        "open": _num(row[2]) if len(row) > 2 else None,
        "high": _num(row[3]) if len(row) > 3 else None,
        "low": _num(row[4]) if len(row) > 4 else None,
        "last": _num(row[5]) if len(row) > 5 else None,
        "prev_close": _num(row[6]) if len(row) > 6 else None,
        "change": _num(row[7]) if len(row) > 7 else None,
        "volume": _num(row[8]) if len(row) > 8 else None,
        "amount": _num(row[9]) if len(row) > 9 else None,
    }


def fetch_snapshot(
    session: requests.Session,
    list_url: str = SSE_LIST_URL,
    page_size: int = PAGE_SIZE,
    host_tracker: Optional[HostStatusTracker] = None,
    allowed_codes: Optional[set] = None,
) -> Tuple[Optional[datetime], Snapshot]:
    """Fetch all SSE securities of one type in a single snapshot.

    Returns (update_dt, {bare_code: full_record}) or (None, {}).
    The ``update_dt`` comes from the API's date+time fields (the "更新时间" on
    the webpage), not from local clock — consistent with download_sse_trend.

    ``list_url`` selects the asset type (equity/fund/index). When
    ``allowed_codes`` is supplied, rows whose bare code is NOT in the set are
    dropped before entering the snapshot — used for indices to implement
    "only load to existing index" (skip the ~200 SSE indices we don't track).
    """
    first = _fetch_page(session, 0, page_size, list_url=list_url, host_tracker=host_tracker)
    if first is None:
        return None, {}
    update_dt = _extract_update_datetime(first)
    if update_dt is None:
        return None, {}

    snapshot: Snapshot = {}
    for row in first.get("list", []) or []:
        rec = _parse_snapshot_row(row)
        if rec and (allowed_codes is None or rec["code"] in allowed_codes):
            snapshot[rec["code"]] = rec

    total = int(first.get("total", 0))
    written = len(first.get("list", []) or [])
    page_index = 1
    while written < total:
        if host_tracker and host_tracker.is_blocked(list_url):
            logger.warning(
                "SSE host blocked mid-pagination (%s), using partial snapshot (%d codes)",
                list_url, len(snapshot),
            )
            break
        begin = page_index * page_size
        end = begin + page_size
        _time.sleep(INTER_PAGE_SLEEP_SEC)
        payload = _fetch_page(session, begin, end, list_url=list_url, host_tracker=host_tracker)
        if payload is None:
            logger.warning("page %d (begin=%d) failed; using partial snapshot", page_index + 1, begin)
            break
        page_rows = payload.get("list", []) or []
        if not page_rows:
            break
        for row in page_rows:
            rec = _parse_snapshot_row(row)
            if rec and (allowed_codes is None or rec["code"] in allowed_codes):
                snapshot[rec["code"]] = rec
        written += len(page_rows)
        page_index += 1

    return update_dt, snapshot


def write_snapshot_csv(
    update_dt: datetime,
    snapshot: Snapshot,
    csv_subdir: str = "sse_intraday",
    csv_prefix: str = "sse_intraday",
) -> Path:
    """Append one polled snapshot to the daily CSV file under temps/<subdir>/.

    One file per trading day, named <prefix>_YYYYMMDD.csv (using the server
    update date). Every poll appends rows to the same daily file; the header
    is written only when the file is created (or is empty). Volume and amount
    are stored raw (shares / yuan) as returned by the endpoint.
    """
    out_dir = resolve_out_dir(__file__, csv_subdir, None)
    ds = update_dt.strftime("%Y%m%d")
    out_file = out_dir / f"{csv_prefix}_{ds}.csv"
    iso = update_dt.strftime("%Y-%m-%d %H:%M:%S")

    needs_header = (not out_file.exists()) or out_file.stat().st_size == 0
    with open(out_file, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if needs_header:
            writer.writeheader()
        for code, rec in snapshot.items():
            row = {"update_time": iso, "code": code}
            for col in CSV_COLUMNS:
                if col in ("update_time", "code"):
                    continue
                row[col] = rec.get(col)
            writer.writerow(row)
    return out_file


# ---------------------------------------------------------------------------
# 5-minute OHLCV aggregation
# ---------------------------------------------------------------------------
def aggregate_bars(
    asset: AssetStream,
    trade_date,
    etf_member_codes: Optional[set] = None,
) -> Tuple[List[dict], List[dict], Optional[time]]:
    """Aggregate 5 one-minute samples into per-security OHLCV bars.

    Drives off the ``asset`` context (AssetStream): stock bars carry a
    per-bar volume + code_suffix and land in stats.stock_intraday_5min; index
    bars carry NO volume / code_suffix and land in stats.index_intraday_5min
    (bare 6-digit code). The ``asset.buffer`` / ``prev_bar_cumvol`` /
    ``finished_codes`` mutable state is read and updated in place.

    Args:
        asset: AssetStream whose buffer holds the samples to aggregate.
        trade_date: datetime.date for the bars.
        etf_member_codes: optional set of full codes (e.g. "600000.SS") that
            are currently in any ETF (latest sec_composition snapshot,
            weight_pct > 0.1). Stocks only: identity rows get
            is_in_index_or_etf=full_code in etf_member_codes; ignored for indices.

    Returns (identity_rows, bar_rows, bar_time).
      * identity_rows feed asset.identity_table (satisfies the FK).
      * bar_rows feed asset.intraday_table.
    """
    buffer = asset.buffer
    if not buffer:
        return [], [], None

    last_dt = buffer[-1][0]
    # Bar end time = last sample's clock time, truncated to the minute.
    bar_time = last_dt.time().replace(second=0, microsecond=0)

    # Collect every code seen across the window.
    all_codes: set = set()
    for _, snap in buffer:
        all_codes.update(snap.keys())

    is_stock = asset.code_suffix == "SS"
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
            # (stocks only — indices have no volume baseline).
            if asset.has_volume and cumvols:
                asset.prev_bar_cumvol[code] = cumvols[-1]
            continue

        o = lasts[0]
        h = max(lasts)
        low = min(lasts)
        c = lasts[-1]

        change = round(c - o, 4)
        change_pct = round((c - o) / o * 100, 4) if o else None

        if is_stock:
            # Stocks: derive per-bar volume from cumulative volume delta, and
            # store code WITH exchange suffix (e.g. "600000.SS").
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
            # Exchange suffix: SZ, SS, BJ, or HK are valid.
            parts = full_code.rsplit(".", 1)
            code_suffix = parts[-1] if len(parts) == 2 and parts[-1] in ("SZ", "SS", "BJ", "HK") else None
            identity_row = {"date": trade_date, "code": full_code, "code_suffix": code_suffix, "name": name}
            if etf_member_codes is not None:
                identity_row["is_in_index_or_etf"] = full_code in etf_member_codes
            identity_rows.append(identity_row)
            bar_rows.append({
                "date": trade_date,
                "code": full_code,
                "code_suffix": code_suffix,
                "time": bar_time,
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": vol,
                "change": change,
                "change_pct": change_pct,
            })
        else:
            # Indices: bare 6-digit code, NO volume / code_suffix columns
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


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _ensure_conn(conn):
    """Return a live psycopg connection, reconnecting if closed."""
    if conn is None or getattr(conn, "closed", False):
        logger.info("Reconnecting to database …")
        return get_db_connection()
    return conn


def _prepopulate_finished_codes(
    conn,
    trade_date,
    finished_codes: set,
    table: str = "stats.stock_intraday_5min",
    code_suffix_filter: Optional[str] = "SS",
) -> None:
    """Query an intraday table to find securities that already have a 15:00
    bar for trade_date. Their bare codes are added to finished_codes.

    This is called at startup to prevent re-processing securities if the
    script restarts after the market has closed.

    ``table`` selects the intraday table (stock vs index). ``code_suffix_filter``
    restricts to one exchange for stocks ('SS'); pass None for indices
    (index_intraday_5min has no code_suffix column and codes are already bare).
    """
    query = f"SELECT DISTINCT code FROM {table} WHERE date = %s AND time = %s"
    params: list = [trade_date, CLOSE_TIME]
    if code_suffix_filter is not None:
        query += " AND code_suffix = %s"
        params.append(code_suffix_filter)
    try:
        with conn.cursor() as cur:
            cur.execute(query, tuple(params))
            for r in cur.fetchall():
                full_code = r[0]
                # Strip the exchange suffix to get bare code (no-op for indices).
                bare = full_code.split(".")[0]
                finished_codes.add(bare)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to pre-populate finished_codes from %s: %s", table, e)


def load_existing_index_codes(conn) -> set:
    """Return the set of bare 6-digit index codes already tracked in
    stats.index_identity.

    SSE index snapshots are filtered to this set so we only stream intraday
    bars for indices we already have daily history for (SSE publishes ~200
    indices; the rest are skipped). Returns an empty set on error (which
    would effectively disable index streaming).
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT code FROM stats.index_identity")
            return {r[0] for r in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to load existing index codes from index_identity: %s", e)
        return set()


def sync_index_has_intraday_flag(conn, trade_date) -> int:
    """Mark stats.index_basic_stats.has_intraday_5mins = TRUE for every
    (date, code) that now has a bar in stats.index_intraday_5min for
    trade_date. Mirrors build_csindex.sync_has_intraday_flag but scoped to a
    single date (the streaming service only ever touches today's bars).
    """
    sql = """
        UPDATE stats.index_basic_stats bs
           SET has_intraday_5mins = TRUE
          FROM (SELECT DISTINCT code FROM stats.index_intraday_5min
                 WHERE date = %s) sub
         WHERE bs.date = %s AND bs.code = sub.code
           AND bs.has_intraday_5mins IS NOT TRUE
    """
    n = 0
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (trade_date, trade_date))
            n = cur.rowcount
        conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to sync index has_intraday_5mins flag: %s", e)
    return n


# ETF membership / target-stock-list helpers live in _study_and_select_stocks
# (imported above): ETF_WEIGHT_THRESHOLD, load_etf_member_codes.


def load_bars(conn, asset: AssetStream, identity_rows: List[dict], bar_rows: List[dict]) -> None:
    """Upsert identity rows (FK parent) then intraday bars for one asset type.

    Dedup identity rows by (date, code) — aggregate_bars emits one identity
    row per bar, so duplicate keys in a single INSERT ... ON CONFLICT raise
    "cannot affect row a second time".
    """
    if identity_rows:
        seen = set()
        uniq = []
        for r in identity_rows:
            k = (r["date"], r["code"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        bulk_upsert(conn, asset.identity_table, uniq, ["date", "code"])
    if bar_rows:
        bulk_upsert(conn, asset.intraday_table, bar_rows, ["date", "code", "time"])


# ---------------------------------------------------------------------------
# Main streaming loop
# ---------------------------------------------------------------------------
def stream(
    poll_interval: float = DEFAULT_POLL_INTERVAL_SEC,
    bar_window: int = DEFAULT_BAR_WINDOW,
    once: bool = False,
    enable_index: bool = True,
) -> None:
    session = build_default_session(SSE_HEADERS)
    host_tracker = HostStatusTracker()

    conn = get_db_connection()
    logger.info("DB ready (stats.stock_intraday_5min / stats.index_intraday_5min expected to pre-exist).")

    # Load the current ETF-member set once per session (snapshots change
    # quarterly). Used by aggregate_bars to set is_in_index_or_etf on new stock identity
    # rows so they don't default to FALSE before the next backfill runs.
    t0 = _time.time()
    etf_member_codes = load_etf_member_codes(conn)
    logger.info(
        "Loaded %d ETF-member codes (latest snapshot, weight_pct > %.1f%%) in %.1fs",
        len(etf_member_codes), ETF_WEIGHT_THRESHOLD, _time.time() - t0,
    )

    # Build the asset-stream contexts. Stocks stream unconditionally; indices
    # are gated on --enable-index (default on) and filtered to codes already
    # present in stats.index_identity ("only load to existing index").
    stock_asset = AssetStream(
        name="stock",
        list_url=SSE_LIST_URL,
        identity_table="stats.stock_identity",
        intraday_table="stats.stock_intraday_5min",
        code_suffix="SS",
        has_volume=True,
        allowed_codes=None,
        csv_subdir="sse_intraday",
        csv_prefix="sse_intraday",
    )
    assets: List[AssetStream] = [stock_asset]

    if enable_index:
        t0 = _time.time()
        existing_index_codes = load_existing_index_codes(conn)
        logger.info(
            "Loaded %d existing index codes from stats.index_identity in %.1fs",
            len(existing_index_codes), _time.time() - t0,
        )
        if existing_index_codes:
            index_asset = AssetStream(
                name="index",
                list_url=SSE_INDEX_LIST_URL,
                identity_table="stats.index_identity",
                intraday_table="stats.index_intraday_5min",
                code_suffix=None,
                has_volume=False,
                allowed_codes=existing_index_codes,
                csv_subdir="sse_index_intraday",
                csv_prefix="sse_index_intraday",
            )
            assets.append(index_asset)
        else:
            logger.warning(
                "No existing index codes found in stats.index_identity; "
                "index streaming disabled (run build_csindex.py first to seed "
                "index_identity, then restart this service)."
            )

    current_trade_date = None

    # Pre-populate finished_codes from DB: securities that already have a
    # 15:00 bar for today. This prevents re-processing if the script restarts
    # after close. Done per asset (stock filters code_suffix='SS'; index has
    # no code_suffix column).
    today = datetime.now().date()
    if is_trading_day(today):
        for asset in assets:
            suffix_filter = "SS" if asset.name == "stock" else None
            t0 = _time.time()
            _prepopulate_finished_codes(
                conn, today, asset.finished_codes,
                table=asset.intraday_table, code_suffix_filter=suffix_filter,
            )
            logger.info(
                "Pre-populated %d finished %s codes from DB for %s in %.1fs",
                len(asset.finished_codes), asset.name, today, _time.time() - t0,
            )
        # Set current_trade_date to avoid clearing finished_codes on first iteration.
        current_trade_date = today

    logger.info(
        "stream_sse_price started (poll=%.0fs bar_window=%d once=%s assets=[%s])",
        poll_interval, bar_window, once, ",".join(a.name for a in assets),
    )

    try:
        while True:
            now = datetime.now()

            # Outside trading hours: flush any partial buffers, then wait.
            if not (is_trading_day(now.date()) and in_trading_hours(now)):
                for asset in assets:
                    if not asset.buffer:
                        continue
                    logger.info("Session ended; flushing %d partial %s samples.", len(asset.buffer), asset.name)
                    update_dt = asset.buffer[-1][0]
                    trade_date = update_dt.date()
                    identity_rows, bar_rows, bar_time = aggregate_bars(
                        asset, trade_date, etf_member_codes=etf_member_codes,
                    )
                    if bar_rows:
                        conn = _ensure_conn(conn)
                        load_bars(conn, asset, identity_rows, bar_rows)
                        logger.info(
                            "flushed %d %s bars for %s %s",
                            len(bar_rows), asset.name, trade_date, bar_time,
                        )
                    asset.buffer.clear()
                if once:
                    logger.info("--once set and outside trading hours; exiting.")
                    break
                nxt = next_trading_moment(now)
                wait = (nxt - now).total_seconds()
                logger.info("Outside trading hours; waiting %.0fs until %s", wait, nxt)
                sleep_until(nxt)
                continue

            # New trading day: reset per-asset streaming state.
            if current_trade_date != now.date():
                current_trade_date = now.date()
                for asset in assets:
                    asset.prev_bar_cumvol.clear()
                    asset.finished_codes.clear()
                    asset.buffer.clear()
                logger.info("New trading day %s; per-asset state reset.", current_trade_date)

            cycle_start = _time.time()
            once_done = False  # set when --once emits a bar this cycle
            for asset in assets:
                update_dt, snapshot = fetch_snapshot(
                    session,
                    list_url=asset.list_url,
                    host_tracker=host_tracker,
                    allowed_codes=asset.allowed_codes,
                )

                if update_dt is None or not snapshot:
                    logger.warning("Poll returned no %s data; skipping cycle.", asset.name)
                    continue

                asset.buffer.append((update_dt, snapshot))
                csv_path = write_snapshot_csv(
                    update_dt, snapshot,
                    csv_subdir=asset.csv_subdir, csv_prefix=asset.csv_prefix,
                )
                logger.info(
                    "sample %d/%d @ %s: %d %ss -> %s",
                    len(asset.buffer), bar_window,
                    update_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    len(snapshot), asset.name,
                    csv_path.name,
                )
                if len(asset.buffer) >= bar_window:
                    trade_date = update_dt.date()
                    identity_rows, bar_rows, bar_time = aggregate_bars(
                        asset, trade_date, etf_member_codes=etf_member_codes,
                    )
                    if bar_rows:
                        conn = _ensure_conn(conn)
                        load_bars(conn, asset, identity_rows, bar_rows)
                        logger.info(
                            "emitted %d %s bars for %s %s (vol baseline=%d codes)",
                            len(bar_rows), asset.name, trade_date, bar_time,
                            len(asset.prev_bar_cumvol),
                        )
                        # Index bars landed: sync the has_intraday_5mins flag
                        # on index_basic_stats for this date so the frontend
                        # knows intraday data is available (replaces the former
                        # csindex.sync_has_intraday_flag post-build step).
                        if asset.name == "index":
                            n = sync_index_has_intraday_flag(conn, trade_date)
                            if n:
                                logger.info("synced has_intraday_5mins=TRUE for %d index rows", n)
                    asset.buffer.clear()
                    if once:
                        logger.info("--once set; exiting after first bar.")
                        once_done = True
                        break

            if once_done:
                break

            # Sleep for the remainder of the poll interval to keep cadence.
            elapsed = _time.time() - cycle_start
            sleep_sec = poll_interval - elapsed
            if sleep_sec > 0:
                _time.sleep(sleep_sec)
    except KeyboardInterrupt:
        logger.info("Interrupted by user; exiting.")
    finally:
        try:
            conn.close()
        except Exception:
            pass
        session.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Stream SSE prices into 5-min OHLCV bars (stocks + indices).")
    ap.add_argument("--interval", type=float, default=DEFAULT_POLL_INTERVAL_SEC,
                    help=f"Poll interval in seconds (default {DEFAULT_POLL_INTERVAL_SEC}).")
    ap.add_argument("--bar-window", type=int, default=DEFAULT_BAR_WINDOW,
                    help=f"Samples per 5-min bar (default {DEFAULT_BAR_WINDOW}).")
    ap.add_argument("--once", action="store_true",
                    help="Emit one bar then exit (dev/test).")
    ap.add_argument("--no-index", action="store_true",
                    help="Stream stocks only (skip the 指数 tab / index_intraday_5min).")
    args = ap.parse_args()
    stream(
        poll_interval=args.interval,
        bar_window=args.bar_window,
        once=args.once,
        enable_index=not args.no_index,
    )


if __name__ == "__main__":
    main()
