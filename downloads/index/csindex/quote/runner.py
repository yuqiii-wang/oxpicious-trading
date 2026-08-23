"""Main orchestrator for the csindex.com.cn quote download pipeline.

Flow per index:
  1. Download full-range daily history via export Excel, from ``start_date``
     (default 2020-01-01) to today (skip if already cached).
  2. Download 1-month daily history via export Excel (incremental update;
     skip if xlsx already fetched today, append missing dates to the csv).
  3. Fetch PE (peg) series for the full range (skip if cached and fresh today after 17:00).
  4. Merge full-range + 1m + PE into ``{indexCode}_history.csv``.
  5. Fetch intraday granular ticks for the latest trading day (skip if unavailable).

Targeted mode (``ensure_prev_trading_day=True`` — used by the "Build Yday
Ref" UI button chain): compute the PREVIOUS trading day from the holiday
calendar and, per code, skip ALL network work when the local 1m/history
CSV already contains that date. Only codes MISSING the prev-day row run
the per-code pipeline (steps 1-2 + history merge; PE/intraday stay owned
by the nightly run). Turns the common case (nightly 19:00 run already
fetched yday) into a seconds-long local check instead of a ~500-code
full sweep.
"""
from __future__ import annotations

import datetime as _dt
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from _common._holidays_and_weekdays import is_trading_day

from downloads._common.core import (
    MIN_VALID_BYTES,
    DEFAULT_START_DATE,
    AntiBotProxy,
    AntiBotConfig,
    RunStats,
    resolve_out_dir,
    parse_date_window,
    is_valid_file,
    is_fresh_today,
    convert_xlsx_to_csv,
    load_classification_index_names,
)

from ._config import (
    CSINDEX_BASE,
    CSINDEX_SKIP_CODES,
    UPDATE_WINDOW_DAYS,
    SLEEP_SEC,
    logger,
    build_session,
    make_proxy,
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
    _find_date_column,
    clean_date,
)


def _csv_has_date(out_dir: Path, code: str, yyyymmdd: str) -> bool:
    """True iff the code's local 1m or history CSV contains the date.

    Optimized: reads only the header + last ~500KB of each CSV instead of
    the full file. CSVs are append-only in chronological order, so the
    target date (prev trading day) is always near the end.

    Pure local check (small recent-window files) — the targeted mode's
    per-code cost. Missing/unreadable files count as NOT having the date.
    """
    import io
    import os

    for fname in (f"{code}_1m.csv", f"{code}_history.csv"):
        f = out_dir / fname
        if not f.is_file():
            continue
        try:
            fsize = f.stat().st_size
            if fsize == 0:
                continue

            # Read header (first line) — needed for column names
            with open(f, "r", encoding="utf-8") as fh:
                header = fh.readline().strip()

            # Read last portion of file (append-only → recent dates at end)
            tail_size = min(fsize, 500_000)  # ~500KB covers many rows
            with open(f, "rb") as fh:
                fh.seek(-tail_size, 2)
                if fsize > tail_size:
                    fh.readline()  # skip first partial line to align to row boundary
                tail_data = fh.read().decode("utf-8")

            df = pd.read_csv(io.StringIO(header + "\n" + tail_data))
            col = _find_date_column(df)
            if not col:
                continue
            if (df[col].apply(clean_date) == yyyymmdd).any():
                return True
        except Exception:
            continue
    return False


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

    # Load index names from sec_classification.json (replaces _classification.py).
    _index_names = load_classification_index_names()

    _start, _end = parse_date_window(start_date=start_date)
    update_end = _end
    update_start = _end - timedelta(days=update_days)

    # Targeted mode: resolve the prev trading day ONCE (calendar-walk).
    target_yyyymmdd: Optional[str] = None
    if ensure_prev_trading_day:
        live = _dt.date.today()
        while not is_trading_day(live):
            live -= timedelta(days=1)
        prev = live - timedelta(days=1)
        while not is_trading_day(prev):
            prev -= timedelta(days=1)
        target_yyyymmdd = prev.strftime("%Y%m%d")
        logger.info(
            "Targeted mode: ensuring prev trading day %s is present in local "
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

            # Targeted fast path: local CSVs already have the prev trading
            # day → nothing to fetch for the yday-ref purpose.
            if (
                target_yyyymmdd is not None
                and _csv_has_date(out_dir, code, target_yyyymmdd)
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

            _run_1m(session, code, update_start, update_end, out_dir, proxy, stats)

            if target_yyyymmdd is not None:
                # Targeted mode stops after the daily rows: PE / history
                # merge / intraday are nightly-owned; the build reads the
                # 1m CSV directly.
                continue

            if proxy.is_blocked(CSINDEX_BASE):
                logger.warning("  [host-blocked] csindex.com.cn blocked after 1m download, skipping remaining tasks for %s", code)
                stats.failed += 2
                continue

            pe_records = _run_pe(session, code, _start, _end, out_dir, proxy, stats)

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
) -> None:
    """Step 2: 1-month export (incremental update window).

    Skip re-downloading the xlsx if it was already fetched today (checked via
    mtime). The xlsx is downloaded with auto_convert disabled so the companion
    csv is NOT overwritten; instead we append only the dates missing from the
    existing csv, letting the 1m csv accumulate recent history across runs.
    """
    onem_xlsx = out_dir / f"{code}_1m.xlsx"
    onem_csv = onem_xlsx.with_suffix(".csv")

    if is_fresh_today(onem_xlsx, min_bytes=MIN_VALID_BYTES, hour=0):
        logger.info("  [1m] %s: xlsx already downloaded today, skipping download", code)
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
) -> List[Dict[str, Any]]:
    """Step 3: PE series (incremental: skip already-fetched dates).

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

    # Skip fetch entirely if cache is fresh today after 17:00 (already up-to-date)
    pe_cache_fresh = (
        is_fresh_today(pe_cache_file, min_bytes=MIN_VALID_BYTES, hour=17)
        and bool(existing_by_date)
    )

    if pe_cache_fresh:
        pe_records = list(existing_by_date.values())
        logger.info("  [pe] %s: cached and fresh (%d records), skipping fetch", code, len(pe_records))
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
