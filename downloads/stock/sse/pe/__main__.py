"""Weekly SSE stock PE anchor crawl — the SSE PE download service.

Service split (2026-11-23): the former ``downloads.stock.sse.trend_pe``
(the archive module's PE half migrated to the trend side on 2026-11-20)
became its OWN ``downloads.stock.sse.pe`` service — price (the daily
``downloads.stock.sse.trend`` trend download) and PE now audit, pace and
gate under separate names, so the weekly cadence keys on THIS service's
own audit history.

The archive module is the OHLCV/dayk backfill only; THIS service owns
every scheduled PE anchor fetch except the daily latest-missing-tail sweep
(``downloads.stock.sse.pe_latest``, which imports its PE helpers from
here — same import pattern the archive module used to serve).

Weekly anchor crawl — per stock, one sweep over the union of:
  (a) Quarterly snapshot dates — first trading day of Feb, Apr, May, Aug,
      Sep, Nov (QUARTERLY_MONTHS), full history from 2020, filtered to
      the stock's listing (first stats.stock_basic_stats date) and to the
      dates still missing from ``{code}_pe.csv`` (no-data dates from a
      previous sweep are retried, as in the archive crawl).
  (b) Big-jump dates — days where the close-to-close return exceeds ±7%
      (JUMP_THRESHOLD_PCT) inside the trailing JUMP_WINDOW_DAYS window;
      for each jump, PE is requested for BOTH the previous day and the
      jump day; a 7-day cooldown (JUMP_COOLDOWN_DAYS) suppresses
      subsequent jumps. The window's closes come from
      stats.stock_basic_stats — the archive module computed jumps from
      its full dayk history; here only the trailing window matters
      (older jump dates are already fetched, or were marked no-data by
      the backfill and are not worth re-requesting weekly).
  (c) The latest missing trading date ≤ target (pe_latest's per-stock
      step) — so this task ALONE keeps the stock-PE estimation ladder
      (``PE_ESTIMATE_MAX_MONTHS = 3`` in builds.stock) valid even if the
      nightly pe_latest sweep is down.

Output: appends full-schema rows (PE_FULL_COLUMNS) to the per-stock
``temps/sse_archive/{code}_pe.csv`` files — same location and format the
archive crawl wrote and builds.stock's PE loader globs; the dirname keeps
its historical name, only the owning module moved to the trend side.

API strategy: alternative endpoint first (commonQuery.do), primary
fallback (queryNewAllQuatAbel.do) — the primary stopped serving usable
rows in mid-2026, so paying its request+sleep first would double the
sweep time for nothing (pe_latest's rationale; when the primary recovers
the fallback still catches it). Both failing marks the date no-data; the
next weekly sweep retries it.

Weekly gate: the scheduled run no-ops while the last NORMAL exit in
monitor.service_audit is younger than WEEKLY_GATE_DAYS calendar days (the
nightly chain fires the downloads_weekly tier every trading day, so the
gate is what makes off-cadence fires no-ops). ``--force`` and ``--code``
bypass the gate; no audit history (first run) proceeds by design.

Universe — IMPORTANT stocks only (2026-11-20): the sweep loads exactly the
curated top ranks of the stock importance ladder, ranks 0-1 of
``database/sec_statics/importance_rules/stock_<rank>_rank.json`` (rank 0
mega heavyweights, rank 1 heavyweights — 77 SSE codes today), resolved via
the ``_common.importance`` rank map (cache-first). Non-important stocks'
``{code}_pe.csv`` files keep their fetched history on disk but stop
getting fresh anchors — builds.stock keeps estimating their PE from the
last actual anchor for up to ``PE_ESTIMATE_MAX_MONTHS`` (3), after which
their daily pe goes NULL. ``--code`` still probes any single stock.

Entry point: ``python -m downloads.stock.sse.pe`` — a DownloadJob
whose universe loader (:func:`load_important_stocks`) is shared with
``downloads.stock.sse.pe_latest``.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from _common._holidays_and_weekdays import is_trading_day
from downloads._common import (
    DEFAULT_START_DATE,
    AntiBotProxy,
    DownloadJob,
    antibot_config_for,
    build_headers_with_referer,
    days_since_last_normal_exit,
    last_business_day,
    next_business_day,
    resolve_out_dir,
    service_urls,
)
from downloads._common.exchanges.sse import (
    CSV_ENCODING,
    JSONP_CALLBACK,
    _parse_jsonp,
)
from _common.db_commons import get_db_connection
from _common.importance import (
    fetch_importance_ranks_or_empty,
    load_importance_ranks_cache,
)
from _common.log_setup import setup_logging  # noqa: E402

logger = setup_logging("sse_pe")

SERVICE = "downloads.stock.sse.pe"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)
# Primary endpoint: queryNewAllQuatAbel.do (FUNDID/inMonth/inYear/
# searchDate params) — long historical coverage (back to 2020 and
# earlier), but it stopped serving usable rows in mid-2026, so it is the
# FALLBACK here (see module docstring).
SSE_TRADE_STATS_URL = URLS["sse_trade_stats_url"]
# Alternative (preferred) endpoint: commonQuery.do with the 成交统计
# sqlId studied from the SSE company page's 成交统计 section
# (``.../company/index.shtml``). Uses SEC_CODE/TX_DATE params and tends
# to cover only recent dates — the quarterly-history dates it cannot
# serve still succeed via the primary fallback.
SSE_TRADE_STATS_ALT_URL = URLS["sse_trade_stats_alt_url"]
SSE_TRADE_STATS_ALT_SQL_ID = "COMMON_SSE_CP_GPJCTPZ_GPLB_CJGK_MRGK_C"
SSE_COMPANY_REFERER = URLS["sse_company_referer"]
SSE_TRADE_STATS_HEADERS = build_headers_with_referer(
    SSE_COMPANY_REFERER, extra={"Accept": "*/*"}
)

# Per-stock PE files live under temps/sse_archive/ — the dirname keeps
# its historical name (builds.stock's PE loader globs it); only the
# owning module moved to the trend side.
ARCHIVE_DIRNAME = "sse_archive"

# First trading day of these months is the quarterly snapshot date.
# User requested: Feb, May, Aug, Nov (quarterly) + Apr, Sep (extra).
QUARTERLY_MONTHS: Tuple[int, ...] = (2, 4, 5, 8, 9, 11)
# PE file holds both metrics per the user's request.
PE_COLUMNS: List[str] = [
    "日期", "证券代码", "证券简称", "静态市盈率(倍)", "总换手率(%)",
]
# Canonical on-disk schema of the {code}_pe.csv files: PE_COLUMNS plus
# the static classification columns the stock build's PE loader filters
# on (``sec_type == "stock"``; ``证券代码`` carries the ".SS" suffix).
PE_FULL_COLUMNS: List[str] = PE_COLUMNS + ["exchange", "board", "sec_type"]
# Jump detection: request PE for prev day + jump day when the daily move
# exceeds this threshold; after each jump-triggered request, subsequent
# jumps within the cooldown are suppressed.
JUMP_THRESHOLD_PCT = 7.0
JUMP_COOLDOWN_DAYS = 7
# The weekly sweep only re-scans this trailing calendar window for jumps
# (a jump straddling the window's first day is missed; the daily
# pe_latest tail fetches that boundary date anyway).
JUMP_WINDOW_DAYS = 21

# The weekly orchestration gate: the scheduled run no-ops while the last
# normal exit (monitor.service_audit) is younger than this many calendar
# days — the nightly chain fires the downloads_weekly tier every trading
# day, so the gate is what makes off-cadence fires no-ops. ``--force``
# and ``--code`` bypass (explicit runs).
WEEKLY_GATE_DAYS = 6

# "Important stocks" = the hand-curated top ranks of the stock importance
# ladder: ranks 0-1 of database/sec_statics/importance_rules/
# stock_<rank>_rank.json (rank 0 mega heavyweights, rank 1 heavyweights),
# materialized verbatim by the monthly builds.classification.importance
# run into stats.sec_importance_ranks (the computed ladder never assigns
# 0-1; it starts at 2). The PE sweeps load ONLY these.
IMPORTANT_MAX_RANK = 1


# ---------------------------------------------------------------------------
# Universe — the IMPORTANT SSE stocks (shared with pe_latest)
# ---------------------------------------------------------------------------
def load_important_stocks(conn) -> List[Tuple[str, str]]:
    """Return ``[(bare_code, name), ...]`` for the IMPORTANT SSE stocks.

    Important = importance rank <= IMPORTANT_MAX_RANK (the curated rank 0-1
    lists of the importance_rules stock JSONs). The rank map is read
    cache-first (``temps/importance/sec_importance_ranks.json``, rewritten
    by the monthly importance build) with the DB table as fallback — the
    ``_common.importance`` download-tier contract, so a DB outage during a
    download run can neither fail the sweep nor empty the universe.

    Names resolve from the latest SS ``stock_identity`` snapshot in one
    query; a code without a name row keeps ``""`` (the trade-stats API's
    productName fills the CSV row anyway). Ordered by (importance_rank,
    code) so a throttled/interrupted sweep has covered the mega-heavyweights
    first. Returns [] (with a warning) only when no rank data exists at all
    — fresh-install callers then simply have nothing to do.
    """
    ranks: Dict[str, int] = load_importance_ranks_cache() or (
        fetch_importance_ranks_or_empty(conn)
    )
    ranked: List[Tuple[str, int]] = sorted(
        ((code, rank) for code, rank in ranks.items()
         if rank <= IMPORTANT_MAX_RANK and code.endswith(".SS")),
        key=lambda cr: (cr[1], cr[0]),
    )
    if not ranked:
        logger.warning(
            "importance rank map empty (no cache, DB unavailable?) — no "
            "important stocks resolved, nothing to sweep",
        )
        return []
    codes = [code for code, _rank in ranked]

    names: Dict[str, str] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(date) FROM stats.stock_identity WHERE exchange = 'SS'"
        )
        latest_id_date = cur.fetchone()[0]
        if latest_id_date is not None:
            cur.execute(
                "SELECT code, name FROM stats.stock_identity "
                "WHERE code = ANY(%s) AND date = %s",
                (codes, latest_id_date),
            )
            names = {code: name or "" for code, name in cur.fetchall()}

    stocks: List[Tuple[str, str]] = [
        (code.split(".")[0], names.get(code, "")) for code in codes
    ]
    logger.info(
        "Loaded %d IMPORTANT SSE stocks (importance rank <= %d, curated "
        "importance_rules JSONs) — the only stocks the PE sweeps load.",
        len(stocks), IMPORTANT_MAX_RANK,
    )
    return stocks


# ---------------------------------------------------------------------------
# Trading statistics (成交统计) helpers — moved verbatim from
# downloads.stock.sse.archive (the module's former PE half)
# ---------------------------------------------------------------------------
def _compute_quarterly_dates(
    start_year: int, end_year: int
) -> List[date]:
    """First trading day of Feb, Apr, May, Aug, Sep, Nov for each year in range.

    Returns a sorted (ascending) list of trading dates. Future dates (after
    today) are excluded since the SSE endpoint returns no data for them.
    """
    today = date.today()
    dates: List[date] = []
    for year in range(start_year, end_year + 1):
        for month in QUARTERLY_MONTHS:
            first_trading = next_business_day(date(year, month, 1))
            if first_trading > today:
                continue
            dates.append(first_trading)
    dates.sort()
    return dates


def _fetch_trade_stats_primary(
    session: requests.Session,
    code: str,
    tx_date: date,
    proxy: AntiBotProxy,
) -> Optional[Dict[str, Any]]:
    """Query the primary SSE 成交概况 endpoint (queryNewAllQuatAbel.do).

    Returns the first result dict (daily data containing
    ``closeProfitRate`` / ``totalExchRate``) or None when the endpoint returns
    an empty result (non-trading day, stock not yet listed, or fetch failure).
    """
    params = {
        "jsonCallBack": JSONP_CALLBACK,
        "FUNDID": code,
        "inMonth": tx_date.strftime("%Y%m"),
        "inYear": tx_date.strftime("%Y"),
        "searchDate": tx_date.isoformat(),
    }
    resp = proxy.get(
        session,
        SSE_TRADE_STATS_URL,
        params=params,
        headers=SSE_TRADE_STATS_HEADERS,
        logger=logger,
        log_tag=f"[trade-stats {code} {tx_date.isoformat()}]",
    )
    if resp is None:
        return None
    payload = _parse_jsonp(resp.text)
    result_arr = payload.get("result") or []
    if not result_arr:
        return None
    # result[0] = daily data, result[1] = monthly, result[2] = yearly
    raw = result_arr[0]
    # Validate: closeTxDate must be a non-empty YYYY-MM-DD string matching the
    # requested date. The API sometimes returns a result with null/empty
    # closeTxDate and 0.0 values — skip those.
    close_tx = raw.get("closeTxDate")
    if not close_tx or not isinstance(close_tx, str):
        return None
    expected = tx_date.isoformat()
    if close_tx != expected:
        logger.debug(
            "  [trade-stats %s %s] closeTxDate mismatch: got %s",
            code, expected, close_tx,
        )
        return None
    return raw


def _fetch_trade_stats_alt(
    session: requests.Session,
    code: str,
    tx_date: date,
    proxy: AntiBotProxy,
) -> Optional[Dict[str, Any]]:
    """Query the alternative SSE 成交统计 endpoint (commonQuery.do).

    Uses the ``COMMON_SSE_CP_GPJCTPZ_GPLB_CJGK_MRGK_C`` sqlId from the
    company page's 成交统计 section (``.../company/index.shtml``). Returns a
    dict normalized to the same key names as the primary API
    (``closeTxDate`` / ``closeProfitRate`` / ``totalExchRate`` /
    ``productName``) so ``_parse_trade_stats_row`` works unchanged, or None
    on failure / empty result / date mismatch.
    """
    params = {
        "jsonCallBack": JSONP_CALLBACK,
        "sqlId": SSE_TRADE_STATS_ALT_SQL_ID,
        "SEC_CODE": code,
        "TX_DATE": tx_date.isoformat(),
    }
    resp = proxy.get(
        session,
        SSE_TRADE_STATS_ALT_URL,
        params=params,
        headers=SSE_TRADE_STATS_HEADERS,
        logger=logger,
        log_tag=f"[trade-stats-alt {code} {tx_date.isoformat()}]",
    )
    if resp is None:
        return None
    payload = _parse_jsonp(resp.text)
    result_arr = payload.get("result") or []
    if not result_arr:
        return None
    raw = result_arr[0]
    # TX_DATE comes back as "YYYYMMDD"; convert to "YYYY-MM-DD" and validate.
    tx_raw = raw.get("TX_DATE")
    if not tx_raw or not isinstance(tx_raw, str) or len(tx_raw) != 8:
        return None
    tx_str = f"{tx_raw[:4]}-{tx_raw[4:6]}-{tx_raw[6:8]}"
    expected = tx_date.isoformat()
    if tx_str != expected:
        logger.debug(
            "  [trade-stats-alt %s %s] TX_DATE mismatch: got %s",
            code, expected, tx_str,
        )
        return None
    # Normalize to primary API key names so downstream parsing is shared.
    return {
        "closeTxDate": tx_str,
        "closeProfitRate": raw.get("PE_RATE"),
        "totalExchRate": raw.get("TO_RATE"),
        "productName": raw.get("SEC_NAME"),
    }


def _parse_trade_stats_row(
    raw: Dict[str, Any], code: str, name: str
) -> Dict[str, Any]:
    """Map one SSE 成交概况 result dict to the PE CSV row schema.

    Returns a single dict with both 静态市盈率(倍) and 总换手率(%).
    Field mapping (from queryNewAllQuatAbel.do result[0] = daily data):
      closeTxDate  → 日期 (already "YYYY-MM-DD")
      closeProfitRate → 静态市盈率(倍)
      totalExchRate → 总换手率(%)
    """
    date_str = str(raw.get("closeTxDate", ""))

    def _val(key: str) -> Any:
        v = raw.get(key)
        if v is None or v == "":
            return ""
        try:
            return round(float(v), 4)
        except (TypeError, ValueError):
            return v

    return {
        "日期": date_str,
        "证券代码": code,
        "证券简称": raw.get("productName") or name,
        "静态市盈率(倍)": _val("closeProfitRate"),
        "总换手率(%)": _val("totalExchRate"),
    }


def _pe_csv_row(raw: Dict[str, Any], bare: str, name: str) -> Dict[str, str]:
    """成交概况 result dict → one full-schema PE CSV row (canonical 8 cols).

    Moved from pe_latest when the PE helpers re-homed here: both sweeps
    append identical rows, so the loader's ``sec_type == "stock"`` filter
    and the ".SS"-suffixed 证券代码 stay consistent.
    """
    row = _parse_trade_stats_row(raw, bare, name)
    row["证券代码"] = f"{bare}.SS"
    row["exchange"] = "SS"
    row["board"] = "STAR" if bare.startswith("68") else "MAIN"
    row["sec_type"] = "stock"
    return row


def _detect_jump_dates(
    ohlcv_rows: List[Dict[str, Any]],
    threshold_pct: float = JUMP_THRESHOLD_PCT,
    cooldown_days: int = JUMP_COOLDOWN_DAYS,
) -> List[date]:
    """Find dates where daily move (drop OR rise) > threshold, with cooldown.

    Scans the OHLCV history for days where the absolute close-to-close return
    exceeds ``threshold_pct`` (i.e. ``|pct| > threshold`` — both big drops and
    big rises). The return is computed from consecutive 今收 (close) prices —
    the 涨跌幅（%）field from the dayk endpoint is often empty because the API
    does not always return ``prev_close``.

    For each jump, collects both the previous day and the jump day as PE
    request dates. After a jump-triggered request, subsequent jumps within
    ``cooldown_days`` calendar days are skipped.

    Returns a sorted list of dates (prev day + jump day pairs).
    """
    jump_pe_dates: List[date] = []
    last_jump_anchor: Optional[date] = None

    def _close_val(row: Dict[str, Any]) -> Optional[float]:
        v = row.get("今收")
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    for i in range(1, len(ohlcv_rows)):
        prev_close = _close_val(ohlcv_rows[i - 1])
        curr_close = _close_val(ohlcv_rows[i])
        if prev_close is None or curr_close is None or prev_close == 0:
            continue
        pct = (curr_close - prev_close) / prev_close * 100
        # Detect BOTH big drops AND big rises (abs > threshold).
        if abs(pct) <= threshold_pct:
            continue

        try:
            curr_date = date.fromisoformat(ohlcv_rows[i]["交易日期"])
            prev_date = date.fromisoformat(ohlcv_rows[i - 1]["交易日期"])
        except (KeyError, ValueError):
            continue

        # Cooldown: skip jumps too close to the last requested jump.
        if last_jump_anchor is not None:
            if (curr_date - last_jump_anchor).days <= cooldown_days:
                continue

        jump_pe_dates.append(prev_date)
        jump_pe_dates.append(curr_date)
        last_jump_anchor = curr_date

    jump_pe_dates.sort()
    return jump_pe_dates


def _read_existing_trade_stats(path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    """Read a trade-stats CSV and return {date_str: row}.

    Used to skip already-fetched dates on resumable runs. Returns an empty
    dict when ``path`` is None or the file does not exist / is invalid.
    """
    existing: Dict[str, Dict[str, Any]] = {}
    if path is None or not path.exists():
        return existing
    try:
        with open(path, "r", encoding=CSV_ENCODING, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = (row.get("日期") or "").strip()
                # Skip invalid dates (e.g., "None", empty, or non-YYYY-MM-DD)
                try:
                    date.fromisoformat(d)
                except ValueError:
                    continue
                existing[d] = row
    except (OSError, csv.Error):
        pass
    return existing


def _write_stats_csv(
    path: Path, rows: List[Dict[str, Any]], columns: List[str]
) -> None:
    """(Re)write a CSV, sorted ascending by date."""
    rows_sorted = sorted(rows, key=lambda r: r.get("日期", ""))
    with open(path, "w", encoding=CSV_ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows_sorted:
            writer.writerow(r)
    # canonicalize 证券代码 -> "NNNNNN.SS" + exchange/board/sec_type columns
    # (no-op for files without a 证券代码 column)
    from downloads._common import ensure_canonical_csv
    ensure_canonical_csv(path, "SS", sec_type="stock")


# ---------------------------------------------------------------------------
# DB lookups — the listing filter and the jump-detection closes (one
# PK-scoped query each); the weekly gate's clock is the shared
# downloads._common gate helper (days_since_last_normal_exit)
# ---------------------------------------------------------------------------
def _load_first_dates(conn, codes: List[str]) -> Dict[str, date]:
    """First stats.stock_basic_stats date per code — the listing filter for
    quarterly snapshot dates (pre-listing dates are never requested)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT code, MIN(date) FROM stats.stock_basic_stats "
            "WHERE code = ANY(%s) GROUP BY code",
            (codes,),
        )
        return dict(cur.fetchall())


def _load_window_closes(
    conn, codes: List[str], window_start: date
) -> Dict[str, List[Dict[str, Any]]]:
    """Trailing-window closes per stock — the jump-detection input.

    Rows are shaped for ``_detect_jump_dates`` (交易日期 / 今收 keys). The
    archive crawl derived jumps from its full dayk history; the weekly
    increment only needs the trailing JUMP_WINDOW_DAYS window.
    """
    closes: Dict[str, List[Dict[str, Any]]] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT code, date, close::float8 AS close "
            "FROM stats.stock_basic_stats "
            "WHERE code = ANY(%s) AND date >= %s AND close IS NOT NULL "
            "ORDER BY code, date",
            (codes, window_start),
        )
        for code, d, close in cur.fetchall():
            closes.setdefault(code.split(".")[0], []).append(
                {"交易日期": d.isoformat(), "今收": close}
            )
    return closes


class SseStockPeDownload(DownloadJob):
    """DownloadJob: weekly PE anchor sweep for SSE stocks (the archive
    module's former PE half, migrated to the trend side)."""

    description = ("Weekly SSE stock PE anchor crawl for the IMPORTANT "
                   "stocks only (curated importance rank 0-1): quarterly "
                   "snapshot dates + trailing-window big-jump dates + the "
                   "latest missing trading date per stock, appended to "
                   "temps/sse_archive/{code}_pe.csv. Own weekly gate "
                   "(>= 6 days since the last normal exit) no-ops "
                   "off-cadence fires; --force/--code bypass.")
    service = SERVICE  # single-instance lock — overlapping sweeps would double-fetch the same (code, date)

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--target-date", type=str, default=None,
            help="PE date ceiling (YYYY-MM-DD; default: the latest "
                 "business day). Quarterly/jump/latest-tail dates beyond "
                 "it are not requested.",
        )
        parser.add_argument(
            "--sleep-sec", type=float, default=None,
            help="Override the trade_stats pacing sleep seconds "
                 "(default: external_request_config JSON for "
                 f"{SERVICE}).",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Bypass the weekly gate (and re-attempt missing "
                 "quarterly dates even when the gate would no-op).",
        )
        parser.add_argument(
            "--code", type=str, default=None,
            help="Single-stock scoped run (bare or .SS-suffixed code) — "
                 "bypasses the weekly gate; a manual probe/backfill.",
        )

    # ------------------------------------------------------------------
    # Fetch cycle
    # ------------------------------------------------------------------
    def fetch_pe_anchors(
        self,
        target: date,
        archive_dir: Path,
        sess: requests.Session,
        stats_proxy: AntiBotProxy,
        stocks: List[Tuple[str, str]],
        all_quarterly_dates: List[date],
        first_dates: Dict[str, date],
        window_closes: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """One weekly sweep: per stock, fetch every still-missing PE date
        from the union of quarterly snapshots (listing-filtered),
        trailing-window big jumps, and the latest missing trading date.

        Dates already in ``{code}_pe.csv`` cost only the in-memory read; a
        failed fetch (both APIs empty) leaves the date missing — the next
        weekly sweep retries it. Returns the summary dict logged by run().
        """
        fetched = 0
        skipped_current = 0
        new_files = 0
        no_data: List[str] = []
        blocked_early = False

        for bare, name in stocks:
            pe_file = archive_dir / f"{bare}_pe.csv"
            existing = _read_existing_trade_stats(pe_file)

            # --- (a) quarterly snapshots missing from the file ---------
            # Listing filter: the DB's first stats row when present;
            # otherwise the file's own earliest row (a stock never built
            # into the DB yet starts at the target date — pre-listing
            # quarterly dates are never requested).
            first_date: date
            db_first = first_dates.get(f"{bare}.SS")
            if db_first is not None:
                first_date = db_first
            elif existing:
                first_date = date.fromisoformat(min(existing))
            else:
                first_date = target

            pe_date_set: set = {
                d for d in all_quarterly_dates
                if first_date <= d <= target
                and d.isoformat() not in existing
            }

            # --- (b) big-jump dates in the trailing window -------------
            for jd in _detect_jump_dates(window_closes.get(bare, [])):
                if jd <= target and jd.isoformat() not in existing:
                    pe_date_set.add(jd)

            # --- (c) the latest missing trading date <= target ---------
            # (pe_latest's per-stock step — keeps the estimation ladder
            # valid even when the nightly sweep is down)
            cursor = target
            while cursor >= date(target.year - 1, 1, 1):
                if is_trading_day(cursor) and cursor.isoformat() not in existing:
                    pe_date_set.add(cursor)
                    break
                cursor -= timedelta(days=1)

            dates_to_fetch = sorted(pe_date_set)
            if not dates_to_fetch:
                skipped_current += 1
                continue

            if stats_proxy.is_blocked(SSE_TRADE_STATS_URL):
                logger.warning(
                    "  [host-blocked] query.sse.com.cn blocked — stopping "
                    "sweep at %s (remaining stocks stay missing)",
                    bare,
                )
                blocked_early = True
                break

            if not pe_file.exists():
                # Create the file with its header before the first append
                # (mirrors pe_latest's resumable file bootstrap).
                _write_stats_csv(pe_file, [], PE_FULL_COLUMNS)
                new_files += 1

            for tx_date in dates_to_fetch:
                if stats_proxy.is_blocked(SSE_TRADE_STATS_URL):
                    logger.warning(
                        "  [host-blocked] stopping mid-stock %s", bare,
                    )
                    blocked_early = True
                    break

                # Alternative first, primary fallback (pe_latest's
                # ordering): the primary stopped serving usable rows in
                # mid-2026, so paying its request+sleep first doubles the
                # sweep time for nothing; when it recovers, the fallback
                # still catches it.
                raw = _fetch_trade_stats_alt(sess, bare, tx_date, stats_proxy)
                used_alt = True
                if raw is None:
                    raw = _fetch_trade_stats_primary(
                        sess, bare, tx_date, stats_proxy,
                    )
                    used_alt = False
                if raw is None:
                    # Both APIs returned nothing (PE not published for the
                    # date, suspended stock, or fetch failure) — leave
                    # missing; the next weekly sweep retries.
                    logger.info(
                        "  -> %s pe %s: FAILED (no data, both APIs)",
                        bare, tx_date.isoformat(),
                    )
                    if bare not in no_data:
                        no_data.append(bare)
                    continue

                row = _pe_csv_row(raw, bare, name)
                with open(pe_file, "a", encoding=CSV_ENCODING, newline="") as f:
                    writer = csv.DictWriter(
                        f, fieldnames=PE_FULL_COLUMNS, extrasaction="ignore"
                    )
                    writer.writerow(row)
                fetched += 1

                api_tag = "alt" if used_alt else "primary"
                logger.info(
                    "  -> %s pe %s [%s]: PE=%s, turnover=%s",
                    bare, tx_date.isoformat(), api_tag,
                    row.get("静态市盈率(倍)", ""),
                    row.get("总换手率(%)", ""),
                )
            if blocked_early:
                break

        return {
            "target_date": target.isoformat(),
            "stocks": len(stocks),
            "fetched": fetched,
            "skipped_current": skipped_current,
            "new_files": new_files,
            "no_data": len(no_data),
            "no_data_codes": no_data[:25],
            "blocked_early": blocked_early,
        }

    def run(self) -> None:
        """One weekly fetch cycle (default target: the latest business day)."""
        if self.args.target_date:
            target = date.fromisoformat(self.args.target_date)
        else:
            target = last_business_day(date.today())

        archive_dir = resolve_out_dir(str(Path(__file__).resolve()), ARCHIVE_DIRNAME)
        conn = get_db_connection()
        try:
            # ---- weekly gate (the scheduled incremental path only) -----
            if not self.args.force and not self.args.code:
                days_idle = days_since_last_normal_exit(SERVICE)
                if days_idle is not None and days_idle < WEEKLY_GATE_DAYS:
                    logger.info(
                        "weekly gate: last normal exit %.1f day(s) ago "
                        "(< %d) — nothing to do.",
                        days_idle, WEEKLY_GATE_DAYS,
                    )
                    return

            # Universe: the IMPORTANT SSE stocks only — curated rank 0-1 of
            # the importance_rules stock JSONs, via the _common.importance
            # rank map (cache-first; see load_important_stocks). Non-
            # important {code}_pe.csv files keep their history on disk but
            # stop getting fresh anchors.
            stocks = load_important_stocks(conn)

            if self.args.code:
                bare = self.args.code.split(".")[0]
                stocks = [(b, n) for b, n in stocks if b == bare] or [(bare, "")]
                logger.info(
                    "--code scoped run: %s (weekly gate bypassed)", bare,
                )
            if not stocks:
                logger.warning("No target SSE stocks resolved — nothing to do.")
                return

            codes = [f"{bare}.SS" for bare, _ in stocks]
            all_quarterly_dates = _compute_quarterly_dates(
                int(DEFAULT_START_DATE[:4]), target.year,
            )
            first_dates = _load_first_dates(conn, codes)
            window_closes = _load_window_closes(
                conn, codes, target - timedelta(days=JUMP_WINDOW_DAYS),
            )
        finally:
            conn.close()

        sess = requests.Session()
        if self.args.sleep_sec is not None:
            stats_proxy = AntiBotProxy(replace(
                antibot_config_for(SERVICE, "trade_stats"),
                base_sleep_sec=self.args.sleep_sec,
            ))
        else:
            stats_proxy = AntiBotProxy(
                antibot_config_for(SERVICE, "trade_stats")
            )

        logger.info(
            "SSE PE weekly anchor sweep: target=%s, stocks=%d (important "
            "rank 0-1), quarterly_dates=%d, jump_window=%dd, "
            "archive_dir=%s",
            target.isoformat(), len(stocks), len(all_quarterly_dates),
            JUMP_WINDOW_DAYS, archive_dir,
        )
        logger.info(self.fetch_pe_anchors(
            target, archive_dir, sess, stats_proxy, stocks,
            all_quarterly_dates, first_dates, window_closes,
        ))


if __name__ == "__main__":
    SseStockPeDownload().execute()
