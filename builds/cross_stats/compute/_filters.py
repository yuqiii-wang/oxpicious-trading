"""Pair-grain filters: DB-side target-date skip + incremental row filter."""
from __future__ import annotations

import datetime
from typing import Optional, Set

import pandas as pd

from builds.cross_stats.config import TABLE


async def filter_target_dates(conn, target_dates):
    """Return target_dates minus dates already present in TABLE as PAIR
    rows (sec_type='index').

    The dates-map detection in runner.py pre-filters; this catches edge
    cases where dates were partially populated (safety net for COPY).
    sec_type-aware ON PURPOSE: the table hosts BOTH grains — industry
    rows at a date must NOT mask that date's missing pair rows (COPY
    would then conflict-free-skip a date it should have written).
    """
    if target_dates is None or len(target_dates) == 0:
        return target_dates

    rows = await conn.fetch(
        f"SELECT DISTINCT date FROM {TABLE} "
        f"WHERE sec_type = 'index' AND date = ANY($1::date[])",
        sorted(target_dates),
    )
    present = {r["date"] for r in rows}
    missing = set(target_dates) - present
    n_already = len(target_dates) - len(missing)
    if n_already > 0:
        print(f"    -> skip check: {n_already:,} of {len(target_dates):,} "
              f"target dates already have pair rows in {TABLE} (skipped)",
              flush=True)
    return missing


def filter_to_target_rows(
    merged: pd.DataFrame,
    target_dates: Optional[Set[datetime.date]],
    subject_code: str,
    subject_idx: int,
    n_subjects: int,
) -> pd.DataFrame:
    """Filter merged to target_dates rows (incremental mode).

    Rolling windows and the MA5 ratio were computed over the full
    (lookback) history — this only selects surviving rows. Handles both
    datetime64 and object-dtype date columns.
    """
    if target_dates is None or len(target_dates) == 0:
        return merged

    n_before = len(merged)
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
