"""Runner for the BSE index (北证50) daily archive downloader.

Mirrors ``downloads.index.cnindex.archive.runner`` (same content gate, raw
artifact, append-only merge) against BSE's own kline endpoint:

  1. CONTENT gate: skip when the history CSV already covers the newest
     session BSE could have published — ``last_business_day(today-1)``.
  2. Fetch the FULL daily kline (single GET), persist it as the raw
     ``{code}_kline.json`` artifact (backoff anchor + re-conversion source).
  3. Drop today's row while the session is still live (the last kline
     record is TODAY mid-session — appending it would freeze a partial-day
     close, because later runs skip once yesterday is present). After
     16:00 the row is final and kept.
  4. Append the dates missing from ``{code}_history.csv`` (append-only;
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
    setup_logger,
)
from downloads._common.exchanges.bjs import (
    BSE_INDEX_CODES,
    bse_index_kline_rows,
    fetch_bse_index_kline,
)
# The CSIndex history-CSV contract (schema + append/force/gate helpers) is
# shared with the cnindex archive — reuse, never duplicate.
from downloads.index.cnindex.archive._history import (
    append_missing_rows_to_csv,
    csv_has_date,
    write_full_csv,
)

logger = setup_logger("bse_index_archive")

# A row dated TODAY is only appended after the session is over (the kline's
# last record is the live intraday row before close).
SESSION_END_HOUR = 16

# Retry backoff when the expected session is still missing (dead feed must
# not re-hit the network on every run). Same value as cnindex.
REFETCH_MIN_INTERVAL_HOURS = 6.0

SLEEP_SEC = 3.0


def download_bse_index_archive(
    *,
    index_codes: Optional[List[str]] = None,
    force: bool = False,
    sleep_sec: float = SLEEP_SEC,
) -> Dict[str, Any]:
    """Refresh the BSE index daily archive CSVs (incremental by default)."""
    if index_codes is None:
        index_codes = list(BSE_INDEX_CODES)
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "bse_index_archive")

    expected_day = last_business_day(_dt.date.today() - _dt.timedelta(days=1))
    expected_yyyymmdd = expected_day.strftime("%Y%m%d")

    logger.info(
        "Starting BSE index archive download: codes=%s expected session=%s "
        "(force=%s) out=%s",
        index_codes, expected_yyyymmdd, force, out_dir,
    )

    session = requests.Session()
    proxy = AntiBotProxy(AntiBotConfig(rotate_browser_profile=False,
                                       base_sleep_sec=sleep_sec))
    stats = RunStats()

    for code in index_codes:
        name = BSE_INDEX_CODES.get(code, code)
        logger.info("== Index %s (%s) ==", code, name)
        csv_path = out_dir / f"{code}_history.csv"
        raw_path = out_dir / f"{code}_kline.json"

        if not force and csv_has_date(out_dir, code, expected_yyyymmdd):
            logger.info("  [kline] %s: history CSV covers %s, skipping fetch",
                        code, expected_yyyymmdd)
            stats.skipped_cached += 1
            continue
        if not force and is_fresh_within(raw_path,
                                         hours=REFETCH_MIN_INTERVAL_HOURS):
            logger.info("  [kline] %s: missing %s but fetched <%.0fh ago, "
                        "backing off", code, expected_yyyymmdd,
                        REFETCH_MIN_INTERVAL_HOURS)
            stats.skipped_cached += 1
            continue

        records = fetch_bse_index_kline(session, proxy=proxy)
        if not records:
            logger.warning("  [kline] %s: fetch failed or no records", code)
            stats.failed += 1
            continue

        try:
            raw_path.write_text(
                json.dumps({"indexCode": code, "indexName": name,
                            "fetched_at": _dt.datetime.now().isoformat(),
                            "records": records}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("  [kline] %s: could not save raw json: %s", code, e)

        rows = bse_index_kline_rows(records)
        today_iso = _dt.date.today().isoformat()
        if _dt.datetime.now().hour < SESSION_END_HOUR:
            # Live intraday row — appending it would freeze today's partial
            # close (the gate would then skip tonight's final re-append).
            live_rows = [r for r in rows if r["date"] == today_iso]
            rows = [r for r in rows if r["date"] != today_iso]
            if live_rows:
                logger.info("  [kline] %s: dropped %d live row(s) for %s "
                            "(before %02d:00)", code, len(live_rows), today_iso,
                            SESSION_END_HOUR)
        if not rows:
            logger.warning("  [kline] %s: no usable rows after live-row drop",
                           code)
            stats.failed += 1
            continue

        if force:
            n = write_full_csv(rows, csv_path)
            logger.info("  [kline] %s (%s): rewrote %s with %d rows "
                        "(%s ~ %s)", code, name, csv_path.name, n,
                        rows[0]["date"], rows[-1]["date"])
        else:
            n = append_missing_rows_to_csv(rows, csv_path)
            logger.info("  [kline] %s (%s): appended %d new rows to %s "
                        "(api range %s ~ %s)", code, name, n, csv_path.name,
                        rows[0]["date"], rows[-1]["date"])
        stats.downloaded += 1
        stats.files.append(str(csv_path))

    logger.info(
        "Done BSE index archive download. downloaded=%d skipped(cached)=%d "
        "failed=%d out=%s",
        stats.downloaded, stats.skipped_cached, stats.failed, out_dir,
    )
    return stats.to_dict(
        out_dir=str(out_dir),
        index_codes=index_codes,
        start_date="(full history)",
        end_date=expected_yyyymmdd,
    )
