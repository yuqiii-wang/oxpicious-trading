"""Fetch + process one index code from csindex.com.cn.

Fetches intraday ticks via the csindex API, aggregates into 5-min OHLC
bars, archives to CSV (temps/csindex_intraday/), and upserts to DB.
CSV archival ensures data is not lost if the DB connection drops.
"""
from __future__ import annotations

import time as _time
from datetime import datetime
from typing import Optional, Tuple

from downloads._common.core import (
    DEFAULT_SLEEP_SEC,
    AntiBotProxy,
    HostStatusTracker,
    setup_logger,
)
from downloads.index.csindex.quote import (
    CSINDEX_BASE,
    fetch_intraday,
)

from ._aggregate import aggregate_ticks_to_5min
from ._csv_io import write_bars_csv
from ._db import upsert_index_bars

logger = setup_logger("csindex_stream")


def fetch_and_upsert_one(
    session,
    code: str,
    name: str,
    proxy: AntiBotProxy,
    host_tracker: HostStatusTracker,
    conn,
) -> Tuple[int, Optional[object]]:
    """Fetch intraday ticks for one index code from csindex.com.cn,
    aggregate into 5-min bars, archive to CSV, and upsert to DB.

    Returns (n_bars_upserted, latest_bar_time).

    CSV archival happens BEFORE DB upsert so that if the DB upsert fails
    (connection drop, constraint error), the fetched data is still
    recoverable via CSV backfill.
    """
    if proxy.is_blocked(CSINDEX_BASE):
        logger.warning("  [csindex-stream] %s: csindex.com.cn is blocked, skipping", code)
        return 0, None

    t0 = _time.time()
    data = fetch_intraday(session, code, proxy)
    elapsed = _time.time() - t0

    if data is None:
        logger.info("  [csindex-stream] %s: NO DATA in %.1fs", code, elapsed)
        return 0, None

    header = data.get("intraDayHeader") or {}
    tick_list = data.get("intraDayPerfList") or []
    if not tick_list:
        logger.info("  [csindex-stream] %s: no ticks available in %.1fs", code, elapsed)
        return 0, None

    # Parse trade date from header or first tick
    trade_date_raw = (header.get("tradeDate") or "").strip()
    if not trade_date_raw and tick_list:
        trade_date_raw = str(tick_list[0].get("tradeDate") or "").strip()
    try:
        trade_date = datetime.strptime(trade_date_raw[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        trade_date = datetime.now().date()

    # Get index name from tick data (more descriptive than sec_classification)
    tick_name = ""
    if tick_list:
        tick_name = tick_list[0].get("indexName") or ""
    if not tick_name:
        tick_name = name

    identity_rows, bar_rows, latest_time = aggregate_ticks_to_5min(
        code, tick_name, tick_list, trade_date,
    )

    # CSV archival BEFORE DB upsert (so DB failure doesn't lose data)
    csv_path = None
    if bar_rows:
        # Add name to each bar row for CSV archival (bar_rows don't carry it)
        for br in bar_rows:
            br["name"] = tick_name
        csv_path = write_bars_csv(datetime.now(), bar_rows)

    n_bars = 0
    if bar_rows:
        try:
            upsert_index_bars(conn, identity_rows, bar_rows)
            n_bars = len(bar_rows)
            logger.info(
                "  [csindex-stream] %s (%s): %d ticks -> %d bars (latest=%s) in %.1fs; upserted; csv=%s",
                code, tick_name, len(tick_list), n_bars, latest_time, elapsed,
                csv_path.name if csv_path else "(none)",
            )
        except Exception as e:
            logger.error(
                "  [csindex-stream] %s: DB upsert failed: %s (CSV archived for backfill: %s)",
                code, e, csv_path.name if csv_path else "(none)",
            )
    else:
        logger.info(
            "  [csindex-stream] %s (%s): %d ticks -> 0 bars in %.1fs",
            code, tick_name, len(tick_list), elapsed,
        )

    return n_bars, latest_time
