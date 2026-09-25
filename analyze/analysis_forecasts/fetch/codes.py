"""Active-universe and first-data-date loaders
(analyze.analysis_forecasts.fetch.codes)."""
from __future__ import annotations

import logging
from datetime import date
from typing import Set

from _common.build_commons import (
    RECENT_TRADING_DAYS,
    fetch_codes_with_recent_data_async,
    recent_trading_day_cutoff,
)

from analyze.analysis_forecasts.config import SEC_TYPE_IDENTITY_TABLE

logger = logging.getLogger(__name__)


async def fetch_active_codes(conn, sec_type: str) -> Set[str]:
    """Return codes with at least one identity-table row in the last
    RECENT_TRADING_DAYS trading days (delisted / suspended securities are
    excluded from the analysis universe entirely)."""
    identity_table = SEC_TYPE_IDENTITY_TABLE[sec_type]
    cutoff = recent_trading_day_cutoff(RECENT_TRADING_DAYS)
    codes = await fetch_codes_with_recent_data_async(
        conn, identity_table, n_trading_days=RECENT_TRADING_DAYS,
    )
    logger.info(f"      pre-filter: {len(codes):,} {sec_type} codes have "
          f"data in the last {RECENT_TRADING_DAYS} trading days "
          f"(cutoff={cutoff.isoformat()})")
    return codes


# True first-data-date source per sec_type (the base OHLCV table — the
# same rows fetch_analysis_inputs admits via b.close IS NOT NULL; kept
# join-free since min(b.date) needs no adjustment/tech columns).
_FIRST_DATE_SOURCE = {
    "index": "stats.index_basic_stats",
    "etf": "stats.etf_basic_stats",
    "stock": "stats.stock_basic_stats",
}


async def fetch_latest_data_date(conn, sec_type: str) -> date | None:
    """The sec_type's LATEST AVAILABLE DATA DATE (max(date) on the base
    OHLCV table with the same close IS NOT NULL filter the input fetch
    uses) — the ROLLING LATEST snapshot's key and window end.

    Keying on DATA (not ``last_business_day()``) gives the rolling key
    two properties: a key is never written ahead of its data (a
    snapshot is always complete when written), and the key is
    idempotent across weekends / holidays (no new data → no new key →
    the run is a clean no-op). Returns None when the table has no rows
    at all (nothing to compute; the caller skips the sec_type)."""
    rows = await conn.fetch(
        f"SELECT max(b.date) AS latest "
        f"FROM {_FIRST_DATE_SOURCE[sec_type]} b "
        f"WHERE b.close IS NOT NULL",
    )
    return rows[0]["latest"] if rows else None


async def fetch_first_dates(
    conn,
    sec_type: str,
    codes: list[str],
) -> dict[str, date]:
    """Per-code TRUE first data date (min(date) on the base OHLCV table
    with the same close IS NOT NULL filter the input fetch uses).

    The fetched input frame is bounded to the earliest needed window
    start, so a long-history code's first row in the frame is the FETCH
    boundary, not its listing date — deriving first rows from the frame
    would wrongly treat 5-year-history codes as fresh. Returns {} for an
    empty code list; codes absent from the table are simply missing from
    the dict (they map to the never-live sentinel of the live-data gate).
    """
    if not codes:
        return {}
    rows = await conn.fetch(
        f"""
        SELECT b.code, min(b.date) AS first_date
        FROM {_FIRST_DATE_SOURCE[sec_type]} b
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
        GROUP BY b.code
        """,
        sorted(codes),
    )
    return {r["code"]: r["first_date"] for r in rows}
