"""
utils/_holidays_and_weekdays.py — Chinese A-share market calendar.

Central source of truth for trading-day decisions across the project:
  - CN_HOLIDAYS            : set of official public holidays when markets are CLOSED
  - CN_ADJUSTED_WORKDAYS   : set of weekend adjustments when markets are OPEN
  - is_trading_day(d)      : True if markets are open on date ``d``
  - last_business_day(ref) : most recent trading day on or before ``ref``
  - next_business_day(ref) : next trading day on or after ``ref``
  - business_days(s, e)    : list of trading days in [s, e]
  - count_weekdays(s, e)   : number of trading days in [s, e]
  - date_range_backward / date_range_forward : raw calendar-day iterators
  - parse_date_window      : resolve a (start, end) pair from CLI-style args

Previously these lived in ``_download_commons.py``; they were migrated here
so that both build scripts (which import ``_db_commons`` only) and download
scripts can share the same calendar without pulling in HTTP/session helpers.

The holiday table is hand-maintained and covers 2020-01-01 through 2026-12-31.
Update it each January when the State Council publishes the next year's
holiday schedule.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional, Set, Tuple


def _parse_dates(date_strings: List[str]) -> Set[date]:
    return {datetime.strptime(s, "%Y-%m-%d").date() for s in date_strings}


# ---------------------------------------------------------------------------
# Chinese A-share market calendar
# ---------------------------------------------------------------------------
CN_HOLIDAYS: Set[date] = _parse_dates([
    # 2020
    "2020-01-01",
    "2020-01-24", "2020-01-25", "2020-01-26", "2020-01-27", "2020-01-28",
    "2020-01-29", "2020-01-30", "2020-01-31", "2020-02-01", "2020-02-02",
    "2020-04-04", "2020-04-05", "2020-04-06",
    "2020-05-01", "2020-05-02", "2020-05-03", "2020-05-04", "2020-05-05",
    "2020-06-25", "2020-06-26", "2020-06-27",
    "2020-10-01", "2020-10-02", "2020-10-03", "2020-10-04", "2020-10-05",
    "2020-10-06", "2020-10-07", "2020-10-08",
    # 2021
    "2021-01-01",
    "2021-02-11", "2021-02-12", "2021-02-13", "2021-02-14", "2021-02-15", "2021-02-16", "2021-02-17",
    "2021-04-04", "2021-04-05", "2021-04-06",
    "2021-05-01", "2021-05-02", "2021-05-03", "2021-05-04", "2021-05-05",
    "2021-06-14",
    "2021-09-21",
    "2021-10-01", "2021-10-02", "2021-10-03", "2021-10-04", "2021-10-05", "2021-10-06", "2021-10-07",
    # 2022
    "2022-01-01",
    "2022-01-31", "2022-02-01", "2022-02-02", "2022-02-03", "2022-02-04", "2022-02-05", "2022-02-06",
    "2022-04-03", "2022-04-04", "2022-04-05",
    "2022-05-01", "2022-05-02", "2022-05-03", "2022-05-04",
    "2022-06-03", "2022-06-04", "2022-06-05",
    "2022-09-10", "2022-09-11", "2022-09-12",
    "2022-10-01", "2022-10-02", "2022-10-03", "2022-10-04", "2022-10-05", "2022-10-06", "2022-10-07",
    # 2023
    "2023-01-01",
    "2023-01-21", "2023-01-22", "2023-01-23", "2023-01-24", "2023-01-25", "2023-01-26", "2023-01-27",
    "2023-04-05",
    "2023-05-01", "2023-05-02", "2023-05-03", "2023-05-04", "2023-05-05",
    "2023-06-22", "2023-06-23", "2023-06-24",
    "2023-09-29", "2023-09-30",
    "2023-10-01", "2023-10-02", "2023-10-03", "2023-10-04", "2023-10-05", "2023-10-06",
    # 2024
    "2024-01-01",
    "2024-02-10", "2024-02-11", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16", "2024-02-17",
    "2024-04-04", "2024-04-05", "2024-04-06",
    "2024-05-01", "2024-05-02", "2024-05-03", "2024-05-04", "2024-05-05",
    "2024-06-10",
    "2024-09-17",
    "2024-10-01", "2024-10-02", "2024-10-03", "2024-10-04", "2024-10-05", "2024-10-06", "2024-10-07",
    # 2025
    "2025-01-01",
    "2025-01-29", "2025-01-30", "2025-01-31", "2025-02-01", "2025-02-02", "2025-02-03", "2025-02-04",
    "2025-04-04",
    "2025-05-01", "2025-05-02", "2025-05-03", "2025-05-04", "2025-05-05",
    "2025-06-02",
    "2025-09-08",
    "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-04", "2025-10-05", "2025-10-06", "2025-10-07",
    # 2026
    "2026-01-01",
    "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
    "2026-04-06",
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    "2026-06-22",
    "2026-09-28",
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07",
])


CN_ADJUSTED_WORKDAYS: Set[date] = _parse_dates([
    # 2020
    "2020-01-19",
    "2020-04-26",
    "2020-06-28",
    "2020-09-27",
    "2020-10-10",
    # 2021
    "2021-02-07", "2021-02-20",
    "2021-04-03",
    "2021-04-25", "2021-05-08",
    "2021-06-13",
    "2021-09-19",
    "2021-09-26", "2021-10-09",
    # 2022
    "2022-01-29", "2022-01-30",
    "2022-04-02",
    "2022-04-30",
    "2022-09-18",
    "2022-09-25", "2022-10-08",
    # 2023
    "2023-01-28", "2023-01-29",
    "2023-04-08",
    "2023-04-29", "2023-04-30",
    "2023-06-25",
    "2023-10-07", "2023-10-08",
    # 2024
    "2024-02-04", "2024-02-18",
    "2024-04-07",
    "2024-04-28", "2024-05-11",
    "2024-06-08",
    "2024-09-15",
    "2024-09-29", "2024-10-12",
    # 2025
    "2025-01-26", "2025-01-27",
    "2025-04-07",
    "2025-04-27", "2025-05-10",
    "2025-06-01",
    "2025-09-06",
    "2025-09-28", "2025-10-11",
    # 2026
    "2026-02-14", "2026-02-24",
    "2026-04-05",
    "2026-04-26", "2026-05-09",
    "2026-06-20",
    "2026-09-26",
    "2026-09-27", "2026-10-10",
])


# ---------------------------------------------------------------------------
# Trading-day predicates
# ---------------------------------------------------------------------------
def is_trading_day(d: date) -> bool:
    """Return True if the Chinese A-share markets are open on date ``d``.

    Adjusted workdays (CN_ADJUSTED_WORKDAYS) take precedence over the regular
    weekend rule, then holidays (CN_HOLIDAYS) are checked, and finally the
    standard Mon-Fri weekday rule applies.
    """
    if d in CN_ADJUSTED_WORKDAYS:
        return True
    if d in CN_HOLIDAYS:
        return False
    return d.weekday() < 5


def last_business_day(ref: Optional[date] = None) -> date:
    """Return the most recent trading day on or before ``ref`` (default: today).

    Takes into account weekends, Chinese public holidays (CN_HOLIDAYS), and
    adjusted workdays (CN_ADJUSTED_WORKDAYS).
    """
    d = ref if ref is not None else date.today()
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def next_business_day(ref: Optional[date] = None) -> date:
    """Return the next trading day on or after ``ref`` (default: today).

    Takes into account weekends, Chinese public holidays (CN_HOLIDAYS), and
    adjusted workdays (CN_ADJUSTED_WORKDAYS).
    """
    d = ref if ref is not None else date.today()
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def date_range_backward(end_date: date, start_date: date) -> Iterable[date]:
    """Yield dates from ``end_date`` down to ``start_date`` (inclusive)."""
    cur = end_date
    while cur >= start_date:
        yield cur
        cur -= timedelta(days=1)


def date_range_forward(start_date: date, end_date: date) -> Iterable[date]:
    """Yield dates from ``start_date`` up to ``end_date`` (inclusive)."""
    cur = start_date
    while cur <= end_date:
        yield cur
        cur += timedelta(days=1)


def count_weekdays(start_date: date, end_date: date) -> int:
    """Number of trading days in [start_date, end_date] (inclusive)."""
    return sum(1 for d in date_range_backward(end_date, start_date) if is_trading_day(d))


def business_days(start_date: date, end_date: date, *, reverse: bool = True) -> List[date]:
    """List of trading days in [start_date, end_date] (inclusive).

    Args:
        start_date: inclusive lower bound
        end_date: inclusive upper bound
        reverse: if True (default), list is newest-first; else oldest-first
    """
    gen = date_range_backward(end_date, start_date) if reverse else date_range_forward(start_date, end_date)
    return [d for d in gen if is_trading_day(d)]


def parse_date_window(
    *,
    end_date: Optional[str] = None,
    start_date: Optional[str] = None,
    default_end: Optional[date] = None,
    lookback_days: Optional[int] = None,
    lookback_years: Optional[int] = None,
) -> Tuple[date, date]:
    """Resolve a (start_date, end_date) tuple from CLI-style string args.

    Resolution order for ``end_date``:
      1. explicit ``end_date`` string (YYYY-MM-DD)
      2. ``default_end`` date object
      3. ``last_business_day(today)``

    Resolution order for ``start_date``:
      1. explicit ``start_date`` string (YYYY-MM-DD)
      2. ``end - lookback_years * 365 - 30`` if ``lookback_years`` is given
      3. ``end - lookback_days`` if ``lookback_days`` is given
      4. ``end - 3 years - 30 days`` (default 3y lookback)

    Raises ValueError if end_date < start_date.
    """
    today = date.today()
    if end_date:
        _end = datetime.strptime(end_date, "%Y-%m-%d").date()
    elif default_end is not None:
        _end = default_end
    else:
        _end = last_business_day(today)
    if start_date:
        _start = datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        if lookback_years is not None:
            extra_days = int(lookback_years * 365 + 30)
            _start = _end - timedelta(days=extra_days)
        elif lookback_days is not None:
            _start = _end - timedelta(days=lookback_days)
        else:
            _start = _end - timedelta(days=365 * 3 + 30)
    if _end < _start:
        raise ValueError(f"end_date ({_end}) must be >= start_date ({_start})")
    return _start, _end
