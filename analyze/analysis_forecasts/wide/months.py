"""Stat-month specs and window resolution (analyze.analysis_forecasts
.wide.months).

``MonthSpec`` — one target snapshot month (the completed month-end) plus
its inclusive trailing-window bounds. ``MonthWindow`` — the spec resolved
against the (T, C) grid into a [lo, hi) row range. Pure stdlib datetime +
numpy searchsorted: the calendar math deliberately stays off the
cudf.pandas path (the old pd.Timestamp/DateOffset version triggered a
dozen fallbacks per run for what is plain calendar arithmetic).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from analyze.analysis_forecasts.config import N_MONTHS, WINDOW_YEARS


@dataclass(frozen=True)
class MonthSpec:
    """One target stat month: the month-end key plus the inclusive
    start of its trailing window (= month-end - WINDOW_YEARS + 1 day,
    i.e. the window covers exactly WINDOW_YEARS of calendar dates)."""
    stat_month: date
    lower: date  # inclusive window start
    upper: date  # inclusive window end (== stat_month; the RUNNING
                 # month's upper is today — the latest available date)


@dataclass(frozen=True)
class MonthWindow:
    """Resolved grid-row range [lo, hi) of one stat month's window.

    lo_ord is the window start's ABSOLUTE epoch-day ordinal (independent
    of the grid extent) — the full-window gate compares per-code first
    data ordinals against it, because the grid itself may begin after
    the nominal window start (lo clamped to 0) and row-space comparison
    would wrongly pass codes first listed at the grid start."""
    stat_month: date
    lo: int
    hi: int
    lo_ord: int


def _shift_years(d: date, years: int) -> date:
    """Calendar-year shift with Feb-29 clamping (pandas DateOffset
    semantics). Pure stdlib — no cudf.pandas proxy dispatch."""
    y = d.year + years
    try:
        return d.replace(year=y)
    except ValueError:  # Feb 29 in a non-leap target year
        return d.replace(year=y, day=28)


def build_month_specs(
    n_months: int = N_MONTHS,
    window_years: int = WINDOW_YEARS,
) -> list[MonthSpec]:
    """The last ``n_months`` COMPLETED month-ends PLUS the running
    (partial) month as MonthSpec list (ascending, oldest first).

    The running month is keyed at its own month-end (the same key the
    completed snapshot will carry) but its window ends TODAY — the
    latest available data date. It therefore recomputes "till today",
    sits inside the refresh window (deleted + recomputed on every run
    while its forward windows still grow), and after month-end the
    SAME key re-derives with complete data. Window lower = month-end
    - window_years + 1 day (inclusive), so each window spans exactly
    ``window_years`` of calendar dates: (M - window_years, M].
    """
    today = date.today()
    # Last COMPLETED month-end: first-of-current-month - 1 day (even on
    # the month's last day the current month is not yet complete).
    last_me = date(today.year, today.month, 1) - timedelta(days=1)

    specs: list[MonthSpec] = []
    y, m = last_me.year, last_me.month
    for _ in range(n_months):
        me = date(y, m, calendar.monthrange(y, m)[1])
        lower = _shift_years(me, -window_years) + timedelta(days=1)
        specs.append(MonthSpec(me, lower, me))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    specs.reverse()
    # The RUNNING month (newest, so appended after the reverse):
    # month-end key, window bounded at today (on the month's last day
    # this degenerates to the completed spec — the key carries over and
    # the refresh window re-derives it next run).
    me = date(today.year, today.month,
              calendar.monthrange(today.year, today.month)[1])
    specs.append(MonthSpec(
        me, _shift_years(me, -window_years) + timedelta(days=1), today,
    ))
    return specs


def month_row_windows(
    grid_ord: np.ndarray,
    specs: list[MonthSpec],
) -> list[MonthWindow]:
    """Resolve each spec's window to a [lo, hi) range of grid rows via
    binary search on the sorted day-ordinal grid (calendar-accurate —
    no fixed row-count approximation of the 5-year window), plus the
    window start's ABSOLUTE epoch ordinal (lo_ord) for the date-space
    full-window gate."""
    lo_ord = np.array([s.lower for s in specs], dtype="datetime64[D]")
    hi_ord = np.array([s.upper for s in specs], dtype="datetime64[D]")
    lo = np.searchsorted(grid_ord, lo_ord.astype(np.int64), side="left")
    hi = np.searchsorted(grid_ord, hi_ord.astype(np.int64), side="right")
    return [
        MonthWindow(s.stat_month, int(a), int(b), int(o))
        for s, a, b, o in zip(specs, lo, hi, lo_ord.astype(np.int64))
    ]
