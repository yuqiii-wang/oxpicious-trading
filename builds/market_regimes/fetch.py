"""DB reads for builds.market_regimes — the per-(code, date) source fetch.

Fetches the FULL per-code price + trading_amount history for one
sec_type from the stats schema (same price conventions as the forecast
engines' fetch_analysis_inputs — ETF adjusted close, estimated closes
excluded) with the active-universe pre-filter of the retired
market_hypes build. The trailing-moment z-features (compute.py) need
the code's OWN full history, so there is no incremental mode — same
contract as the retired build's fetch.
"""
from __future__ import annotations

import logging

import pandas as pd

from _common._holidays_and_weekdays import recent_trading_day_cutoff
from _common.df_utils import epoch_col_to_dt64
from _common.pre_check_and_load import (
    RECENT_TRADING_DAYS,
    fetch_codes_with_recent_data_async,
)
from builds.market_regimes.config import (
    SEC_TYPE_IDENTITY_TABLE,
    SEC_TYPE_SOURCE_SQL,
)

logger = logging.getLogger(__name__)

# Output columns of fetch_regime_source (the detector's inputs).
REGIME_SOURCE_COLUMNS = ("sec_type", "code", "date", "price",
                         "trading_amount")


async def fetch_regime_source(
    conn,
    sec_type: str,
    *,
    code_filter: str | None = None,
) -> pd.DataFrame:
    """Fetch the sec_type's FULL per-(code, date) price + amount history.

    Pre-filter: only codes with at least one identity-table row in the
    last RECENT_TRADING_DAYS trading days are loaded (delisted /
    suspended codes are excluded — their regimes are stale by
    construction). In ``code_filter`` (single-code) mode the
    pre-filter is bypassed and exactly that code's full history is
    loaded.

    Returns a DataFrame with the REGIME_SOURCE_COLUMNS, sorted by
    (sec_type, code, date) — the order compute_market_regimes requires.
    Empty (with those columns) when the sec_type has no active codes /
    no source data.
    """
    empty = pd.DataFrame(columns=list(REGIME_SOURCE_COLUMNS))
    identity_table = SEC_TYPE_IDENTITY_TABLE[sec_type]

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
        active_codes = {code_filter}
        logger.info(f"      code filter: {code_filter} (single-code mode)")
    if not active_codes:
        return empty

    rows = await conn.fetch(
        SEC_TYPE_SOURCE_SQL[sec_type], sorted(active_codes),
    )
    if not rows:
        return empty

    df = pd.DataFrame(rows, columns=["code", "date", "price",
                                     "trading_amount"])
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df.insert(0, "sec_type", sec_type)
    return (df.sort_values(["sec_type", "code", "date"])
              .reset_index(drop=True))
