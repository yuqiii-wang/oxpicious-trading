"""Step 2 (DB-side filter) and Step 8 (incremental row filter)."""
from __future__ import annotations

import datetime
from typing import Optional, Set

import pandas as pd

from _common.build_commons import find_missing_dates
from analyze.sec_alloc_perf_attribution.config import TABLE


# ---------------------------------------------------------------------------
#  Step 2: DB-side skip for already-present target dates (safety net)
# ---------------------------------------------------------------------------
async def filter_target_dates(conn, target_dates):
    """Return target_dates minus dates already present in TABLE.

    The dates-map-based missing-date detection in run.py already
    filters target_dates, but this catches any edge cases where dates
    were partially populated.
    """
    if target_dates is None or len(target_dates) == 0:
        return target_dates

    missing = await find_missing_dates(conn, TABLE, target_dates)
    n_already = len(target_dates) - len(missing)
    if n_already > 0:
        print(f"    -> skip check: {n_already:,} of {len(target_dates):,} "
              f"target dates already present in {TABLE} (skipped)",
              flush=True)
    return missing


# ---------------------------------------------------------------------------
#  Step 8: incremental-mode row filter
# ---------------------------------------------------------------------------
def filter_to_target_rows(
    merged: pd.DataFrame,
    target_dates: Optional[Set[datetime.date]],
    subject_code: str,
    subject_idx: int,
    n_subjects: int,
) -> pd.DataFrame:
    """Filter merged to only target_dates rows (incremental mode).

    Rolling correlations and the MA5 ratio have already been computed
    over the full history, so the trailing windows are correct. This
    filter just selects which rows survive to the upsert.

    Handles BOTH datetime64 and object-dtype (date) columns by
    converting ``target_dates`` to match the column's dtype.
    """
    if target_dates is None or len(target_dates) == 0:
        return merged

    n_before = len(merged)

    # Normalize comparison: convert target_dates to match column dtype.
    if pd.api.types.is_datetime64_any_dtype(merged["date"]):
        ts_targets = pd.to_datetime(list(target_dates))
        merged = merged[merged["date"].isin(ts_targets)].copy()
    else:
        merged = merged[merged["date"].isin(target_dates)].copy()

    if (subject_idx + 1) % 10 == 0 or (subject_idx + 1) == n_subjects:
        print(f"      {subject_code}: incremental filter "
              f"{len(merged):,} of {n_before:,} rows in target_dates",
              flush=True)
    return merged
