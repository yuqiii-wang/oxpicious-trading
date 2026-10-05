"""Input loaders of the in-engine rsi-pct bucket computation
(analyze.analysis_signals.fetch.inputs).

The signals tier computes its OWN buckets off the raw RSI percentile
data since the forecast layer was removed (2026-10-05): per sec_type
the joined (code, date) frame of price + the RSI window columns
(analysis.mov_ave_rsi — the value source), the active-code universe,
per-code first-data dates (the live gate) and the latest RSI data date
(the ROLLING LATEST snapshot key).

All NUMERIC columns are cast to native float8 in SQL (Decimal objects
would poison the cudf.pandas fast path) and the date columns arrive as
epoch float8, materialized via epoch_col_to_dt64 — the frame the
engine receives is cudf-representable from construction.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Set

from _common.build_commons import (
    RECENT_TRADING_DAYS,
    fetch_codes_with_recent_data_async,
    rec_cols,
    recent_trading_day_cutoff,
)
from _common.df_utils import epoch_col_to_dt64
import pandas as pd

from analyze.analysis_signals.config import SEC_TYPE_IDENTITY_TABLE, RSI_WINDOWS
from analyze.mov_ave_spread.config import OHL_EST_FILTER, PRICE_SOURCE

logger = logging.getLogger(__name__)

RSI_TABLE = "analysis.mov_ave_rsi"

# True first-data-date source per sec_type (the base OHLCV table — the
# same rows the input fetch admits via b.close IS NOT NULL).
_FIRST_DATE_SOURCE = {
    "index": "stats.index_basic_stats",
    "etf": "stats.etf_basic_stats",
    "stock": "stats.stock_basic_stats",
}

_INPUT_COLUMNS = ["code", "date", "price"] + [
    f"rsi_{w}days" for w in RSI_WINDOWS
]


async def fetch_active_codes(conn, sec_type: str) -> Set[str]:
    """Codes with at least one identity-table row in the last
    RECENT_TRADING_DAYS trading days (delisted / suspended securities
    are excluded from the emission universe entirely)."""
    identity_table = SEC_TYPE_IDENTITY_TABLE[sec_type]
    cutoff = recent_trading_day_cutoff(RECENT_TRADING_DAYS)
    codes = await fetch_codes_with_recent_data_async(
        conn, identity_table, n_trading_days=RECENT_TRADING_DAYS,
    )
    logger.info(f"      pre-filter: {len(codes):,} {sec_type} codes have "
          f"data in the last {RECENT_TRADING_DAYS} trading days "
          f"(cutoff={cutoff.isoformat()})")
    return codes


async def fetch_latest_rsi_date(conn, sec_type: str) -> date | None:
    """The sec_type's LATEST AVAILABLE RSI DATA DATE (max(date) on
    analysis.mov_ave_rsi) — the ROLLING LATEST snapshot's key and
    window end. Keying on DATA (not ``last_business_day()``) keeps the
    key idempotent across weekends / holidays (no data → no new key →
    the run is a clean no-op) and never ahead of the value source.
    Returns None when the table has no rows for the sec_type."""
    return await conn.fetchval(
        f"SELECT max(date) FROM {RSI_TABLE} WHERE sec_type = $1",
        sec_type,
    )


async def fetch_first_dates(
    conn,
    sec_type: str,
    codes: list[str],
) -> dict[str, date]:
    """Per-code TRUE first data date (min(date) on the base OHLCV
    table with the same close IS NOT NULL filter the input fetch
    uses) — the live gate's input (a code joins a snapshot from its
    own first-data date)."""
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


async def fetch_rsi_inputs(
    conn,
    sec_type: str,
    codes: list[str],
    since: date,
) -> pd.DataFrame:
    """The joined long-format input frame for the given codes: one row
    per (code, date) with the price (the mov_ave_spread convention —
    ETF adjusted close) + one rsi_{W}days column per RSI window.

    Rows are bounded to date >= ``since`` (the earliest trailing-window
    start across the target snapshots); there is NO upper date bound —
    forward changes for the last bucket days of the newest snapshot
    need post-snapshot-end prices. Codes without a single RSI row are
    dropped by the code pre-filter (the caller intersects the universe
    with the RSI table's codes)."""
    if not codes:
        return pd.DataFrame(columns=_INPUT_COLUMNS)

    base, price_expr = PRICE_SOURCE[sec_type]
    ohl_est_filter = OHL_EST_FILTER[sec_type]
    rsi_cols = ",\n       ".join(
        f"r.rsi_{w}days::float8 AS rsi_{w}days" for w in RSI_WINDOWS
    )
    sql = f"""
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {price_expr}::float8 AS price,
               {rsi_cols}
        FROM {base}
        LEFT JOIN {RSI_TABLE} r
               ON r.sec_type = '{sec_type}'
              AND r.code = b.code AND r.date = b.date
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
          AND b.date >= $2
          {ohl_est_filter}
        ORDER BY b.code, b.date ASC
    """
    rows = await conn.fetch(sql, sorted(codes), since)
    if not rows:
        return pd.DataFrame(columns=_INPUT_COLUMNS)
    # Column-materialized ctor + epoch→datetime64[us] (object python
    # dates would poison every downstream op; ::float8 casts avoid
    # Decimal).
    df = pd.DataFrame(rec_cols(rows), columns=_INPUT_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    return df


async def fetch_rsi_codes(conn, sec_type: str) -> Set[str]:
    """Codes carrying at least one RSI row for the sec_type — the
    universe pre-filter's RSI leg (a code without RSI data can never
    enter a percentile bucket)."""
    rows = await conn.fetch(
        f"SELECT DISTINCT code FROM {RSI_TABLE} WHERE sec_type = $1",
        sec_type,
    )
    return {r["code"] for r in rows}
