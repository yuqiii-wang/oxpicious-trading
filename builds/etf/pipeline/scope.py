"""Step 2 — DB scope resolution: force purge, existing keys, missing dates."""
from __future__ import annotations

import datetime
from typing import Set, Tuple

import pandas as pd

from _common.build_commons import get_existing_keys_async, rec_cols, truncate_table_async
from _common.db_commons import chunked_purge_async
from _common.df_utils import epoch_col_to_dt64

import logging
logger = logging.getLogger(__name__)


async def purge_existing_data(conn, code_filter: str | None) -> None:
    """--force cleanup: DELETE single-code rows or truncate all ETF tables.

    FK children first, identity last.  ``stats.sec_composition`` is shared
    with index composition (source_type='index', owned by
    builds.index.composition) — only ETF rows are ever removed here.
    """
    if code_filter:
        # Single-code force mode: DELETE only this code's rows.
        logger.info(f"    [DB] Force mode for code {code_filter}: deleting existing rows for this code")
        for tbl in ("stats.etf_liquidity_margin", "stats.etf_adjustment",
                    "stats.etf_tech_stats", "stats.etf_basic_stats",
                    "stats.etf_identity"):
            await conn.execute(f"DELETE FROM {tbl} WHERE code = $1", code_filter)
        await conn.execute(
            "DELETE FROM stats.sec_composition WHERE source_type = 'etf' AND code = $1",
            code_filter,
        )
    else:
        logger.info("    [DB] Force mode: truncating ETF tables")
        await truncate_table_async(conn, "stats.etf_identity")
        await chunked_purge_async(
            conn, "stats.sec_composition", where_sql="source_type = 'etf'",
        )


async def fetch_existing_data_keys(
    conn, code_filter: str | None, force: bool
) -> Tuple[set, set]:
    """Fetch existing (date, code) pairs from stats.etf_basic_stats.

    Data presence — NOT stats.etf_identity presence — is the completeness
    signal: the SSE price stream upserts etf_identity rows for the whole
    streamed universe before/during the live session (FK parent of its
    5-min bars), so an identity-keyed check made the daily build skip
    every streamed (date, code) whose OHLCV was never written (the
    2026-10-12 gap: all 1,063 SSE ETFs stuck at the prior date while the
    streamer-pre-seeded identity rows looked "already built").

    Single-code mode queries only that code so dates loaded for OTHER ETFs
    don't mask its gaps.

    Returns: (existing_keys, existing_dates).
    """
    if force:
        return set(), set()
    if code_filter:
        key_rows = await conn.fetch(
            "SELECT date, code FROM stats.etf_basic_stats WHERE code = $1",
            code_filter,
        )
        existing_keys = {(r["date"], r["code"]) for r in key_rows}
    else:
        existing_keys = await get_existing_keys_async(
            conn, "stats.etf_basic_stats", ["date", "code"]
        )
    return existing_keys, {d for (d, _c) in existing_keys}


async def fetch_per_code_day_stats(conn, code_filter: str | None) -> pd.DataFrame:
    """Per-code stored day stats: (code, db_first, db_last, db_n_days).

    The B3 trailing-window DB fetch means ``merged`` no longer covers each
    code's full history, so build_universe's merged-derived first_date /
    last_date / n_ohlcv_days would be window-truncated. These DB aggregates
    (one index-friendly GROUP BY) restore the true values. Empty under
    --force (tables purged — merged then holds the full CSV history).
    """
    if code_filter:
        rows = await conn.fetch(
            "SELECT code, "
            "extract(epoch from MIN(date))::float8 AS db_first, "
            "extract(epoch from MAX(date))::float8 AS db_last, "
            "COUNT(*) AS db_n_days FROM stats.etf_identity WHERE code = $1 "
            "GROUP BY code",
            code_filter,
        )
    else:
        rows = await conn.fetch(
            "SELECT code, "
            "extract(epoch from MIN(date))::float8 AS db_first, "
            "extract(epoch from MAX(date))::float8 AS db_last, "
            "COUNT(*) AS db_n_days FROM stats.etf_identity GROUP BY code"
        )
    if not rows:
        return pd.DataFrame(columns=["code", "db_first", "db_last", "db_n_days"])
    df = pd.DataFrame(rec_cols(rows))
    # Dates stay datetime64[us] (epoch_col_to_dt64 over the float8 epoch
    # aggregates) — build_universe does the min/max comparison on
    # datetimes and strftime's once at the end (np.where over ISO strings
    # would assign an object column — cudf Unsupported-dtype fallback)
    for c in ("db_first", "db_last"):
        df[c] = epoch_col_to_dt64(df[c], index=df.index)
    df["db_n_days"] = df["db_n_days"].astype("int64")  # COUNT → int64 already
    return df


def compute_dates_to_read(
    available_dates: Set[datetime.date],
    existing_dates: Set[datetime.date],
) -> Tuple[Set[datetime.date], Set[datetime.date]]:
    """Missing dates + recent-date re-scan for newly-listed ETFs.

    The re-scan window catches newly-listed ETFs whose (date, code) pairs
    are absent from already-loaded dates within the last RECENT_REFRESH_DAYS.

    Returns: (missing_ohlcv_dates, recent_refresh_dates).
    """
    missing = available_dates - existing_dates
    recent: Set[datetime.date] = set()
    if available_dates:
        cutoff = max(available_dates) - datetime.timedelta(days=RECENT_REFRESH_DAYS)
        recent = {d for d in (available_dates & existing_dates) if d >= cutoff}
    if recent:
        logger.info(f"    [DB] {len(recent)} recent dates (last {RECENT_REFRESH_DAYS}d) "
              f"re-scanned for newly-listed ETFs")
    return missing, recent


RECENT_REFRESH_DAYS: int = 30

# Margin-stale repair window: only recent dates are checked, so a genuinely
# margin-less historical date can never loop the repair. 60d comfortably
# covers the empty-download-marker retry window (EMPTY_MARKER_RETRY_DAYS).
MARGIN_STALE_LOOKBACK_DAYS: int = 60


async def fetch_margin_stale_dates(
    conn,
    available_dates: Set[datetime.date],
    code_filter: str | None,
) -> Set[datetime.date]:
    """Dates (within the recent lookback) whose stored ETF margin is
    all-NULL/zero across every row — the signature of a margin CSV that was
    missing/empty at build time (download failure or not-yet-published).

    Such dates used to keep their baked zero/NULL margin forever because
    write candidacy was gated on OHLCV existence; the caller re-reads these
    dates' CSVs and re-upserts their rows once the CSV carries data.

    Returns only DB-side candidates — the caller gates them against the
    day's margin detail files actually carrying data rows before scheduling
    the repair.
    """
    if not available_dates:
        return set()
    newest = max(available_dates)
    cutoff = newest - datetime.timedelta(days=MARGIN_STALE_LOOKBACK_DAYS)
    scope_dates = sorted(d for d in available_dates if d >= cutoff)
    if not scope_dates:
        return set()

    _code_where = "AND l.code = $2" if code_filter else ""
    rows = await conn.fetch(
        f"""
        SELECT l.date
        FROM stats.etf_liquidity_margin l
        WHERE l.date = ANY($1::date[])
          {_code_where}
        GROUP BY l.date
        HAVING COALESCE(MAX(l.rz_balance), 0) = 0
           AND COALESCE(MAX(l.rz_buy), 0) = 0
           AND COALESCE(MAX(l.rq_sell_qty), 0) = 0
           AND COALESCE(MAX(l.rq_balance_qty), 0) = 0
           AND COALESCE(MAX(l.rq_balance_amt), 0) = 0
           AND COALESCE(MAX(l.total_balance), 0) = 0
        """,
        scope_dates,
        *((code_filter,) if code_filter else ()),
    )
    stale = {r["date"] for r in rows}
    if stale:
        logger.info(f"    [DB] {len(stale)} margin-stale dates (all-NULL/zero stored "
              f"margin, last {MARGIN_STALE_LOOKBACK_DAYS}d) — repair scheduled")
    return stale
