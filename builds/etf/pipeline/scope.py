"""Step 2 — DB scope resolution: force purge, existing keys, missing dates."""
from __future__ import annotations

import datetime
from typing import Set, Tuple

import pandas as pd

from _common.build_commons import get_existing_keys_async, rec_cols, truncate_table_async
from _common.df_utils import epoch_col_to_dt64


async def purge_existing_data(conn, code_filter: str | None) -> None:
    """--force cleanup: DELETE single-code rows or truncate all ETF tables.

    FK children first, identity last.  ``stats.sec_composition`` is shared
    with index composition (source_type='index', owned by
    builds.index.composition) — only ETF rows are ever removed here.
    """
    if code_filter:
        # Single-code force mode: DELETE only this code's rows.
        print(f"    [DB] Force mode for code {code_filter}: deleting existing rows for this code", flush=True)
        for tbl in ("stats.etf_liquidity_margin", "stats.etf_adjustment",
                    "stats.etf_tech_stats", "stats.etf_basic_stats",
                    "stats.etf_identity"):
            await conn.execute(f"DELETE FROM {tbl} WHERE code = $1", code_filter)
        await conn.execute(
            "DELETE FROM stats.sec_composition WHERE source_type = 'etf' AND code = $1",
            code_filter,
        )
    else:
        print("    [DB] Force mode: truncating ETF tables", flush=True)
        await truncate_table_async(conn, "stats.etf_identity")
        await conn.execute("DELETE FROM stats.sec_composition WHERE source_type = 'etf'")


async def fetch_existing_identity_keys(
    conn, code_filter: str | None, force: bool
) -> Tuple[set, set]:
    """Fetch existing (date, code) pairs from stats.etf_identity.

    Single-code mode queries only that code so dates loaded for OTHER ETFs
    don't mask its gaps.

    Returns: (existing_keys, existing_dates).
    """
    if force:
        return set(), set()
    if code_filter:
        key_rows = await conn.fetch(
            "SELECT date, code FROM stats.etf_identity WHERE code = $1",
            code_filter,
        )
        existing_keys = {(r["date"], r["code"]) for r in key_rows}
    else:
        existing_keys = await get_existing_keys_async(
            conn, "stats.etf_identity", ["date", "code"]
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
        print(f"    [DB] {len(recent)} recent dates (last {RECENT_REFRESH_DAYS}d) "
              f"re-scanned for newly-listed ETFs", flush=True)
    return missing, recent


RECENT_REFRESH_DAYS: int = 30
