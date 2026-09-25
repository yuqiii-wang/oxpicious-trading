"""Snapshot-date specs (analyze.analysis_forecasts.wide.months).

``StatSpec`` — one target snapshot date plus its inclusive
trailing-window bounds. Pure stdlib datetime (the calendar math
deliberately stays off the cudf.pandas path — the old
pd.Timestamp/DateOffset version triggered a dozen fallbacks per run
for what is plain calendar arithmetic). The former ``StatWindow``
grid-row resolution went with the numpy (T, C) matrix machinery
retired with the opp_pair family in 2026-09.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from analyze.analysis_forecasts.config import (
    N_YEARS,
    WINDOW_YEARS,
    window_lower,
)
# The grid module's private helper re-exported for the throwaway
# wide._shift_years importers (config's `import *` skips underscore
# names).
from analyze.analysis_forecasts.config.grid import _shift_years  # noqa: F401


@dataclass(frozen=True)
class StatSpec:
    """One target snapshot date: the grid key plus the inclusive start
    of its trailing window (= stat_date − WINDOW_YEARS + 1 day, i.e.
    the window covers exactly WINDOW_YEARS of calendar dates)."""
    stat_date: date
    lower: date  # inclusive window start
    upper: date  # inclusive window end (== stat_date; the ROLLING
                 # LATEST spec's upper is the latest available data
                 # date, not a year-end)


def build_stat_specs(
    n_years: int = N_YEARS,
    window_years: int = WINDOW_YEARS,
    *,
    latest_date: date,
) -> list[StatSpec]:
    """The last ``n_years`` COMPLETED year-ends (bounded to year-ends
    the data has actually reached: YE <= latest_date) PLUS the ROLLING
    LATEST spec — keyed at ``latest_date`` (the sec_type's latest
    available data date, NOT the year-end) — ascending, oldest first.

    The rolling latest key moves forward with the data: its window ends
    AT the key (a key is always complete for its data), it is
    idempotent across weekends / holidays (no data → no new key), and
    it sits inside the mutable scope (deleted + recomputed on every
    run — see config.mutable_dates). When ``latest_date`` itself is a
    completed year-end it IS that year-end's spec — one key, deduped,
    seamless New-Year handover. Window lower = window_lower(stat_date),
    so each window spans exactly ``window_years`` of calendar dates:
    (stat_date − window_years, stat_date].
    """
    specs: list[StatSpec] = []
    y = latest_date.year - 1
    while len(specs) < n_years and y >= latest_date.year - n_years:
        ye = date(y, 12, 31)
        if ye > latest_date:
            y -= 1
            continue
        specs.append(StatSpec(ye, window_lower(ye), ye))
        y -= 1
    specs.reverse()
    # The ROLLING LATEST spec (newest, appended after the reverse):
    # keyed at the latest available data date. When that date is itself
    # a completed year-end already collected above, skip the duplicate —
    # the same spec serves as both the completed snapshot and the
    # mutable latest.
    if all(s.stat_date != latest_date for s in specs):
        specs.append(StatSpec(
            latest_date, window_lower(latest_date), latest_date,
        ))
    return specs
