"""ETF membership computation — forward-fill from sec_composition snapshots.

Contains:
- compute_is_in_index_or_etf_async: for each target date, return which stocks
  appear in any ETF's active composition with weight_pct > threshold.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from datetime import date

from _common.build_commons import rec_col

ETF_WEIGHT_THRESHOLD: float = 0.1


async def compute_is_in_index_or_etf_async(
    conn,
    target_dates: set[date],
) -> dict[date, set[str]]:
    """For each target date, return the set of stock codes that appear in any
    ETF's active composition (most recent snapshot on or before the date) with
    weight_pct > ETF_WEIGHT_THRESHOLD.

    Implementation: 2 DB queries + Python binary search.
      1. Fetch all distinct ETF snapshot dates (sorted).
      2. Fetch all (snapshot_date, stock_code) pairs with weight > threshold
      3. For each target date, bisect to the most recent snapshot_date <= target,
         then return the precomputed stock_code set for that snapshot.
    """
    target_dates = sorted(set(target_dates))
    if not target_dates:
        return {}

    snap_rows = await conn.fetch(
        "SELECT DISTINCT snapshot_date "
        "FROM stats.sec_composition "
        "WHERE source_type = 'etf' "
        "ORDER BY snapshot_date"
    )
    snap_dates = rec_col(snap_rows, "snapshot_date")
    if not snap_dates:
        return {d: set() for d in target_dates}

    stock_rows = await conn.fetch(
        "SELECT snapshot_date, stock_code "
        "FROM stats.sec_composition "
        "WHERE source_type = 'etf' "
        "  AND weight_pct > $1 "
        "  AND stock_code IS NOT NULL",
        ETF_WEIGHT_THRESHOLD,
    )
    # Whole-column zip pairing → set aggregation per snapshot (pure host)
    stocks_by_snap: dict[date, set[str]] = defaultdict(set)
    for d, s in zip(rec_col(stock_rows, "snapshot_date"),
                    rec_col(stock_rows, "stock_code")):
        stocks_by_snap[d].add(s)

    result: dict[date, set[str]] = {}
    for td in target_dates:
        idx = bisect.bisect_right(snap_dates, td) - 1
        if idx < 0:
            result[td] = set()
        else:
            result[td] = stocks_by_snap.get(snap_dates[idx], set())
    return result
