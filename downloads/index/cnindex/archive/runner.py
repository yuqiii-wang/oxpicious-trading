"""Runner for the CNINDEX daily archive downloader.

Per code (CNINDEX_ARCHIVE_CODES):
  1. CONTENT gate: skip when the history CSV already covers the newest
     session CSIndex could have published — ``last_business_day(today-1)``.
     The raw-fetch mtime is never a skip reason by itself (publishes land
     at arbitrary times); it only bounds retries via
     REFETCH_MIN_INTERVAL_HOURS so a dead feed doesn't re-hit the network
     on every run.
  2. Fetch the full daily history (single GET — the API always returns the
     whole series), persist it as the raw ``{code}_daily.json`` artifact
     (backoff anchor + re-conversion source, same role as the old
     ``{code}_perf_*.xls`` downloads).
  3. Append the dates missing from ``{code}_history.csv`` (append-only;
     ``--force`` rewrites the CSV entirely from the API).
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from _common._holidays_and_weekdays import last_business_day

from downloads._common import (
    AntiBotConfig,
    AntiBotProxy,
    RunStats,
    is_fresh_within,
    resolve_out_dir,
)

from ._api import fetch_daily_data
from ._config import (
    CNINDEX_ARCHIVE_CODES,
    REFETCH_MIN_INTERVAL_HOURS,
    SLEEP_SEC,
    logger,
)
from ._history import (
    append_missing_rows_to_csv,
    csv_has_date,
    daily_rows_from_bars,
    write_full_csv,
)


def download_cnindex_archive(
    *,
    index_codes: Optional[List[str]] = None,
    force: bool = False,
    sleep_sec: float = SLEEP_SEC,
) -> Dict[str, Any]:
    """Refresh the CNINDEX daily archive CSVs (incremental by default)."""
    if index_codes is None:
        index_codes = list(CNINDEX_ARCHIVE_CODES)
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "cnindex_archive")

    # Newest COMPLETED session whose data CNINDEX may have published:
    # yesterday's session on a trading day, the last session otherwise.
    expected_day = last_business_day(_dt.date.today() - _dt.timedelta(days=1))
    expected_yyyymmdd = expected_day.strftime("%Y%m%d")

    logger.info(
        "Starting cnindex archive download: codes=%s expected session=%s "
        "(force=%s) out=%s",
        index_codes, expected_yyyymmdd, force, out_dir,
    )

    session = requests.Session()
    proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=sleep_sec))
    stats = RunStats()

    for code in index_codes:
        logger.info("== Index %s ==", code)
        csv_path = out_dir / f"{code}_history.csv"
        raw_path = out_dir / f"{code}_daily.json"

        if not force and csv_has_date(out_dir, code, expected_yyyymmdd):
            logger.info("  [daily] %s: history CSV covers %s, skipping fetch",
                        code, expected_yyyymmdd)
            stats.skipped_cached += 1
            continue
        if not force and is_fresh_within(raw_path,
                                         hours=REFETCH_MIN_INTERVAL_HOURS):
            logger.info("  [daily] %s: missing %s but fetched <%.0fh ago, "
                        "backing off", code, expected_yyyymmdd,
                        REFETCH_MIN_INTERVAL_HOURS)
            stats.skipped_cached += 1
            continue

        fetched = fetch_daily_data(session, code, proxy)
        if fetched is None:
            logger.warning("  [daily] %s: fetch failed", code)
            stats.failed += 1
            continue
        index_name, bars = fetched
        if not bars:
            logger.warning("  [daily] %s (%s): API returned no bars", code,
                           index_name)
            stats.failed += 1
            continue

        # Raw artifact: backoff anchor + re-conversion source.
        try:
            raw_path.write_text(
                json.dumps({"indexCode": code, "indexName": index_name,
                            "fetched_at": _dt.datetime.now().isoformat(),
                            "bars": bars}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("  [daily] %s: could not save raw json: %s", code, e)

        rows = daily_rows_from_bars(code, index_name, bars)
        if force:
            n = write_full_csv(rows, csv_path)
            logger.info("  [daily] %s (%s): rewrote %s with %d rows "
                        "(%s ~ %s)", code, index_name, csv_path.name, n,
                        rows[0]["date"] if rows else "?",
                        rows[-1]["date"] if rows else "?")
        else:
            n = append_missing_rows_to_csv(rows, csv_path)
            logger.info("  [daily] %s (%s): appended %d new rows to %s "
                        "(api range %s ~ %s)", code, index_name, n,
                        csv_path.name,
                        rows[0]["date"] if rows else "?",
                        rows[-1]["date"] if rows else "?")
        stats.downloaded += 1
        stats.files.append(str(csv_path))

    logger.info(
        "Done cnindex archive download. downloaded=%d skipped(cached)=%d "
        "failed=%d out=%s",
        stats.downloaded, stats.skipped_cached, stats.failed, out_dir,
    )
    return stats.to_dict(
        out_dir=str(out_dir),
        index_codes=index_codes,
        start_date="(full history)",
        end_date=expected_yyyymmdd,
    )
