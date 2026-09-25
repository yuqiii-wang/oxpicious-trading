"""Main orchestrator for the csindex.com.cn quote download pipeline.

Flow per index:
  1. Download full-range daily history via export Excel, from ``start_date``
     (default 2020-01-01) to today (skip if already cached).
  2. Download 1-month daily history via export Excel (incremental update;
     skip when the local CSVs already cover the newest publishable session,
     append missing dates to the csv).
  3. Fetch PE (peg) series for the full range (skip when the PE cache
     already covers the newest publishable session).
  4. Merge full-range + 1m + PE into ``{indexCode}_history.csv``.
  5. Fetch intraday granular ticks for the latest trading day (skip if unavailable).

CONTENT FRESHNESS, NOT MTIME: the skip decisions for the daily-record
steps (2 and 3) compare the local records against the newest session whose
data CSIndex could have published — ``last_business_day(today - 1)`` —
never against the file mtime. A morning fetch must not pin the whole
calendar day: CSIndex publishes laggard indices' rows hours behind peers,
so a code whose CSVs lack the expected session is re-fetched by every
later run that day (bounded by REFETCH_MIN_INTERVAL_HOURS so indices
CSIndex stopped publishing don't burn the anti-bot sleep budget).

Targeted mode (``ensure_prev_trading_day=True`` — used by the "Build Yday
Ref" UI button chain): only codes MISSING the expected session run the
per-code pipeline (steps 1-2 + history merge; PE/intraday stay owned by
the nightly run). Turns the common case (nightly run already fetched the
previous session) into a seconds-long local check instead of a ~500-code
full sweep.
"""
from __future__ import annotations

import datetime as _dt
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from _common._holidays_and_weekdays import last_business_day

from downloads._common import (
    MIN_VALID_BYTES,
    DEFAULT_START_DATE,
    AntiBotProxy,
    AntiBotConfig,
    RunStats,
    resolve_out_dir,
    parse_date_window,
    is_valid_file,
    is_fresh_within,
    convert_xlsx_to_csv,
    load_classification_index_names,
)

from ._config import (
    CSINDEX_BASE,
    CSINDEX_SKIP_CODES,
    UPDATE_WINDOW_DAYS,
    REFETCH_MIN_INTERVAL_HOURS,
    SLEEP_SEC,
    logger,
    build_session,
    make_proxy,
    ymd,
)
from ._export import download_export_excel
from ._pe import (
    fetch_pe_series,
    load_pe_cache,
    save_pe_cache,
    index_pe_by_date,
)
from ._intraday import fetch_intraday, save_intraday
from ._history import (
    build_history_csv,
    append_missing_dates_to_csv,
    csv_has_date,
)


def download_index(
    *,
    index_codes: Optional[List[str]] = None,
    out_root: Optional[str] = None,
    start_date: str = DEFAULT_START_DATE,
    update_days: int = UPDATE_WINDOW_DAYS,
    sleep_sec: float = SLEEP_SEC,
    skip_intraday: bool = False,
    ensure_prev_trading_day: bool = False,
) -> dict:
    """Download iconic CSI index daily history (OHLCV + amount + PE).

    ``ensure_prev_trading_day``: targeted mode — skip ALL network work for
    codes whose local 1m/history CSV already contains the previous trading
    day (computed from the holiday calendar). Laggard codes run only the
    from2020 (cached-skip) + 1m steps; PE / history-merge / intraday stay
    owned by the nightly full run. The baseline build reads the 1m CSVs
    directly, so yday rows land in the DB without the heavy extras.
    """
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "csindex", out_root)

    if index_codes is None:
        index_codes = list(load_classification_index_names().keys())

    # DUMMY_* codes are synthetic classification placeholders (strategy /
    # broad-market buckets used by builds & analysis), not real CSI indices
    # — csindex.com.cn has no data for them, so every per-code step would
    # only warn and burn anti-bot sleeps (~1 min each).
    _dummy_codes = [c for c in index_codes if c.startswith("DUMMY_")]
    if _dummy_codes:
        logger.info(
            "Skipping %d DUMMY_* placeholder codes (not real CSI indices)",
            len(_dummy_codes),
        )
        index_codes = [c for c in index_codes if not c.startswith("DUMMY_")]

    # Load index names from sec_classification.json (replaces _classification.py).
    _index_names = load_classification_index_names()

    _start, _end = parse_date_window(start_date=start_date)
    update_end = _end
    update_start = _end - timedelta(days=update_days)

    # Newest COMPLETED session whose data CSIndex may have published:
    # yesterday's session when today is a trading day (today's EOD is not
    # assumed), the last session otherwise (weekend/holiday — everything
    # up to it is publishable). ONE expectation for both the targeted-mode
    # target and the per-step content gates below.
    expected_day = last_business_day(_dt.date.today() - _dt.timedelta(days=1))
    expected_yyyymmdd = expected_day.strftime("%Y%m%d")

    # Targeted mode: resolve the expected session ONCE (calendar-aware).
    target_yyyymmdd: Optional[str] = None
    if ensure_prev_trading_day:
        target_yyyymmdd = expected_yyyymmdd
        logger.info(
            "Targeted mode: ensuring session %s is present in local "
            "CSVs (codes already covering it are skipped entirely)",
            target_yyyymmdd,
        )

    logger.info(
        "Starting csindex download: codes=%s window=%s->%s (start=%s) "
        "update=%s->%s out=%s",
        index_codes, _start, _end, start_date,
        update_start, update_end, out_dir,
    )

    session = build_session()
    stats = RunStats()

    proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=sleep_sec))

    try:
        for code in index_codes:
            name = _index_names.get(code, code)

            if code in CSINDEX_SKIP_CODES:
                logger.info("== Index %s (%s) — skipped (in CSINDEX_SKIP_CODES, handled by SZSE downloader) ==", code, name)
                stats.skipped_cached += 1
                continue

            # Targeted fast path: local CSVs already cover the expected
            # session → nothing to fetch for the yday-ref purpose.
            if (
                target_yyyymmdd is not None
                and csv_has_date(out_dir, code, target_yyyymmdd)
            ):
                stats.skipped_cached += 1
                continue

            logger.info("== Index %s (%s) ==", code, name)

            if proxy.is_blocked(CSINDEX_BASE):
                logger.warning("  [host-blocked] csindex.com.cn is blocked, skipping all tasks for %s", code)
                stats.failed += 4
                continue

            _run_from2020(session, code, _start, _end, out_dir, proxy, stats)

            if proxy.is_blocked(CSINDEX_BASE):
                logger.warning("  [host-blocked] csindex.com.cn blocked after from2020 download, skipping remaining tasks for %s", code)
                stats.failed += 3
                continue

            _run_1m(session, code, update_start, update_end, out_dir, proxy,
                    stats, expected_yyyymmdd)

            if target_yyyymmdd is not None:
                # Targeted mode stops after the daily rows: PE / history
                # merge / intraday are nightly-owned; the build reads the
                # 1m CSV directly.
                continue

            if proxy.is_blocked(CSINDEX_BASE):
                logger.warning("  [host-blocked] csindex.com.cn blocked after 1m download, skipping remaining tasks for %s", code)
                stats.failed += 2
                continue

            pe_records = _run_pe(session, code, _start, _end, out_dir, proxy,
                                 stats, expected_yyyymmdd)

            # --- Step 4: Merge into history CSV ---
            history_file = build_history_csv(code, name, out_dir, pe_records)
            if history_file:
                stats.files.append(str(history_file))

            # --- Step 5: Intraday granular ticks (skip if unavailable) ---
            if not skip_intraday:
                intraday_data = fetch_intraday(session, code, proxy)
                if intraday_data is not None:
                    saved = save_intraday(intraday_data, code, name, out_dir)
                    if saved:
                        stats.files.append(str(saved))
                else:
                    logger.info("  [intraday] %s: 1-day data not available, skipping", code)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = stats.to_dict(
        out_dir=str(out_dir),
        index_codes=index_codes,
        start_date=str(_start),
        end_date=str(_end),
        update_days=update_days,
    )
    logger.info(
        "Done csindex download. downloaded=%d skipped(cached)=%d failed=%d out=%s",
        stats.downloaded, stats.skipped_cached, stats.failed, out_dir,
    )
    return summary


def _run_from2020(
    session, code, start, end, out_dir, proxy, stats,
) -> None:
    """Step 1: full-range export (skip if cached)."""
    from2020_file = out_dir / f"{code}_from2020.xlsx"
    from2020_csv_file = from2020_file.with_suffix(".csv")
    from2020_downloaded = False
    if is_valid_file(from2020_file, min_bytes=MIN_VALID_BYTES):
        logger.info("  [from2020] %s already cached, skipping download", code)
        stats.skipped_cached += 1
        if is_valid_file(from2020_csv_file, min_bytes=MIN_VALID_BYTES):
            logger.info("  [from2020] %s already converted, skipping csv conversion", code)
        else:
            convert_xlsx_to_csv(from2020_file, logger=logger, log_tag=f"[from2020 {code}]")
    else:
        ok = download_export_excel(session, code, start, end, from2020_file, proxy)
        from2020_downloaded = ok
        if ok:
            stats.downloaded += 1
            stats.files.append(str(from2020_file))
        else:
            stats.failed += 1
    if from2020_downloaded:
        pass  # Auto-sleep handled by proxy.post() inside download_export_excel


def _run_1m(
    session, code, update_start, update_end, out_dir, proxy, stats,
    expected_yyyymmdd: str,
) -> None:
    """Step 2: 1-month export (incremental update window).

    Content-freshness gate: skip the download only when the local 1m or
    history CSV already contains a row for *expected_yyyymmdd* — the
    newest session CSIndex could have published. The xlsx mtime is NOT a
    skip reason by itself: a fetch from earlier the same day must not pin
    the day, because CSIndex publishes laggard indices' rows hours after
    peers (e.g. overseas indices landing their T-1 row mid-day). A code
    missing the expected session re-fetches on every later run, bounded by
    REFETCH_MIN_INTERVAL_HOURS so never-published indices don't burn the
    anti-bot sleep budget on every attempt.

    The xlsx is downloaded with auto_convert disabled so the companion csv
    is NOT overwritten; the append below merges only the dates missing
    from the existing csv, letting the 1m csv accumulate recent history
    across runs (idempotent — also runs after a skipped download so a
    still-unmerged xlsx from the backoff window is not lost).
    """
    onem_xlsx = out_dir / f"{code}_1m.xlsx"
    onem_csv = onem_xlsx.with_suffix(".csv")

    if csv_has_date(out_dir, code, expected_yyyymmdd):
        logger.info("  [1m] %s: local CSVs cover %s, skipping download", code, expected_yyyymmdd)
        stats.skipped_cached += 1
    elif is_fresh_within(onem_xlsx, hours=REFETCH_MIN_INTERVAL_HOURS,
                         min_bytes=MIN_VALID_BYTES):
        logger.info("  [1m] %s: missing %s but xlsx fetched <%.0fh ago, backing off",
                    code, expected_yyyymmdd, REFETCH_MIN_INTERVAL_HOURS)
        stats.skipped_cached += 1
    else:
        ok = download_export_excel(
            session, code, update_start, update_end, onem_xlsx, proxy,
            auto_convert=False,
        )
        if ok:
            stats.downloaded += 1
            stats.files.append(str(onem_xlsx))
        else:
            stats.failed += 1
    # Auto-sleep handled by proxy.post() inside download_export_excel

    # Append missing dates from the xlsx into the csv (idempotent —
    # safe to run every time even when the download was skipped).
    n_appended = append_missing_dates_to_csv(onem_xlsx, onem_csv, code)
    if n_appended is None:
        logger.warning("  [1m] %s: could not append to csv (xlsx missing/unreadable)", code)
    elif n_appended > 0:
        logger.info("  [1m] %s: appended %d new rows to %s", code, n_appended, onem_csv.name)
        stats.files.append(str(onem_csv))
    else:
        logger.info("  [1m] %s: csv already up to date (0 new rows)", code)


def _run_pe(
    session, code, start, end, out_dir, proxy, stats,
    expected_yyyymmdd: str,
) -> List[Dict[str, Any]]:
    """Step 3: PE series (incremental: skip already-fetched dates).

    Content-freshness gate: skip the fetch when the cache already covers
    *expected_yyyymmdd* (the newest session CSIndex could have published);
    the json mtime is only a REFETCH_MIN_INTERVAL_HOURS retry backoff for
    codes that keep failing the content check. PE publishes with the quote
    data (sometimes hours later for laggard indices), so a cache fetched
    earlier the same day must not pin the day.

    If PE is already present in the cache, only fetch dates newer than the
    latest cached PE date (instead of overwriting the whole history).
    """
    pe_cache_file = out_dir / f"{code}_pe.json"
    pe_records: List[Dict[str, Any]] = []

    # Load existing cache to enable incremental fetch
    existing_pe = load_pe_cache(pe_cache_file) or []
    existing_by_date = index_pe_by_date(existing_pe)

    # Determine fetch start: latest cached PE date (overlap by 1 day to
    # allow override), else full-range start.
    fetch_start = start
    if existing_by_date:
        latest_pe_str = max(existing_by_date.keys())
        try:
            latest_pe_dt = datetime.strptime(latest_pe_str, "%Y%m%d").date()
            fetch_start = latest_pe_dt
            logger.info(
                "  [pe] %s: cache has %d records (latest=%s), incremental fetch %s~%s",
                code, len(existing_by_date), latest_pe_str, fetch_start, end,
            )
        except ValueError:
            pass

    # Content gate: cache covers the newest publishable session → nothing
    # to gain from a fetch. Backoff guard otherwise (never a skip reason
    # on its own — see _run_1m).
    if existing_by_date and max(existing_by_date.keys()) >= expected_yyyymmdd:
        pe_records = list(existing_by_date.values())
        logger.info(
            "  [pe] %s: cache covers %s (latest=%s), skipping fetch",
            code, expected_yyyymmdd, max(existing_by_date.keys()),
        )
        stats.skipped_cached += 1
    elif is_fresh_within(pe_cache_file, hours=REFETCH_MIN_INTERVAL_HOURS,
                         min_bytes=MIN_VALID_BYTES) and existing_by_date:
        pe_records = list(existing_by_date.values())
        logger.info(
            "  [pe] %s: missing %s (latest=%s) but cache fetched <%.0fh ago, backing off",
            code, expected_yyyymmdd, max(existing_by_date.keys()),
            REFETCH_MIN_INTERVAL_HOURS,
        )
        stats.skipped_cached += 1
    else:
        new_records = fetch_pe_series(session, code, fetch_start, end, proxy)
        if new_records:
            # Merge: new records override existing for same date
            new_by_date = index_pe_by_date(new_records)
            existing_by_date.update(new_by_date)
            pe_records = list(existing_by_date.values())
            if save_pe_cache(pe_cache_file, pe_records):
                logger.info(
                    "  [pe] %s: cached to %s (total=%d, fetched=%d new)",
                    code, pe_cache_file.name, len(pe_records), len(new_by_date),
                )
            else:
                logger.info(
                    "  [pe] %s: %d records (fetched %d new, merged total %d)",
                    code, len(pe_records), len(new_by_date), len(pe_records),
                )
            stats.downloaded += 1
        else:
            pe_records = list(existing_by_date.values())
            if pe_records:
                logger.warning(
                    "  [pe] %s: fetch returned no data, using existing cache (%d records)",
                    code, len(pe_records),
                )
            else:
                logger.warning("  [pe] %s: no PE data returned", code)
                stats.failed += 1
    # Auto-sleep handled by proxy.get() inside fetch_pe_series (only when fetched)
    return pe_records
