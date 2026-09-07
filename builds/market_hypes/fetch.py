"""DB reads for builds.market_hypes — source fetch + rolling σ legs.

Fetches the per-(code, date) price + trading_amount source for one
sec_type from the stats schema (same tables / JOIN structure / price
semantics as the mov_ave_spread parent pipeline, see
config.SEC_TYPE_SOURCE_SQL) and computes the std_{W}days rolling
population-σ columns the episode detector checks in on — the same
computation the mov_ave_spread pipeline performs for its detail table
(grouped_rolling_agg, ddof=0, min_periods=W), so the σ values match
what the analysis detail carries.

The centered-20y percentile thresholds make the episode detection a
FULL-HISTORY computation (up to 2550 rows on EACH side of every audited
date), so the fetch is always FULL per-code history — there is no
incremental/target-dates mode.
"""
from __future__ import annotations

import logging

import pandas as pd

from _common._holidays_and_weekdays import recent_trading_day_cutoff
from _common.df_utils import epoch_col_to_dt64, grouped_rolling_agg
from _common.pre_check_and_load import (
    RECENT_TRADING_DAYS,
    fetch_codes_with_recent_data_async,
)
from builds.market_hypes.config import (
    HYPE_CHECKIN_PERIODS,
    HYPE_STD_COLUMN_BY_PERIOD,
    SEC_TYPE_IDENTITY_TABLE,
    SEC_TYPE_SOURCE_SQL,
)

logger = logging.getLogger(__name__)

# Output columns of fetch_hype_source (the episode detector's inputs).
HYPE_SOURCE_COLUMNS = (
    "sec_type", "code", "date", "price", "trading_amount",
    *[HYPE_STD_COLUMN_BY_PERIOD[w] for w in HYPE_CHECKIN_PERIODS],
)


async def fetch_hype_source(
    conn,
    sec_type: str,
    *,
    code_filter: str | None = None,
) -> pd.DataFrame:
    """Fetch the sec_type's FULL per-(code, date) price + trading_amount
    history and add the std_{W}days rolling-σ columns.

    Pre-filter: only codes with at least one identity-table row in the
    last RECENT_TRADING_DAYS trading days are loaded (the same
    active-universe rule the mov_ave_spread pipeline applies). In
    ``code_filter`` (single-code) mode the pre-filter is bypassed and
    exactly that code's full history is loaded.

    Returns a DataFrame with the HYPE_SOURCE_COLUMNS, sorted by
    (sec_type, code, date) — the order compute_market_hypes requires.
    Empty (with those columns) when the sec_type has no active codes /
    no source data.
    """
    empty = pd.DataFrame(columns=list(HYPE_SOURCE_COLUMNS))
    identity_table = SEC_TYPE_IDENTITY_TABLE[sec_type]

    # ---- Pre-filter: keep only codes with recent data ------------------
    cutoff = recent_trading_day_cutoff(RECENT_TRADING_DAYS)
    active_codes = await fetch_codes_with_recent_data_async(
        conn, identity_table, n_trading_days=RECENT_TRADING_DAYS,
    )
    logger.info(
        f"      pre-filter: {len(active_codes):,} {sec_type} codes have "
        f"data in the last {RECENT_TRADING_DAYS} trading days "
        f"(cutoff={cutoff.isoformat()})"
    )
    if code_filter is not None:
        # Single-code mode (--code): bypass the active-universe
        # pre-filter and load exactly this code's full history.
        active_codes = {code_filter}
        logger.info(f"      code filter: {code_filter} (single-code mode)")
    if not active_codes:
        return empty

    rows = await conn.fetch(
        SEC_TYPE_SOURCE_SQL[sec_type], sorted(active_codes),
    )
    if not rows:
        return empty

    df = pd.DataFrame(
        rows, columns=["code", "date", "price", "trading_amount"],
    )
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df.insert(0, "sec_type", sec_type)

    # ---- Rolling population σ legs (one column per check-in window) ----
    # Same computation as analyze.mov_ave_spread.helpers.
    # compute_rolling_stds: grouped_rolling_agg std with ddof=0
    # (Bollinger convention), min_periods=W — NULL until the window is
    # fully populated.
    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    for w in HYPE_CHECKIN_PERIODS:
        df[HYPE_STD_COLUMN_BY_PERIOD[w]] = grouped_rolling_agg(
            df, ["sec_type", "code"], "price",
            window=w, min_periods=w, agg="std", ddof=0,
        )
    return df[list(HYPE_SOURCE_COLUMNS)]
