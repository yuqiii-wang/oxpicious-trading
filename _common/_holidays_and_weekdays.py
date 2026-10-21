"""
_common/_holidays_and_weekdays.py — Chinese A-share market calendar.

Central source of truth for trading-day decisions across the project:
  - CN_HOLIDAYS            : set of dates when the A-share markets are CLOSED
  - CN_ADJUSTED_WORKDAYS   : empty (kept for API compatibility — the market
                             never opens on weekend 调休 make-up days)
  - is_trading_day(d)      : True if markets are open on date ``d``
  - last_business_day(ref) : most recent trading day on or before ``ref``
  - next_business_day(ref) : next trading day on or after ``ref``
  - business_days(s, e)    : list of trading days in [s, e]
  - count_weekdays(s, e)   : number of trading days in [s, e]
  - date_range_backward / date_range_forward : raw calendar-day iterators
  - parse_date_window      : resolve a (start, end) pair from CLI-style args

The holiday TABLE lives in the static JSON tree
database/sql/static/holiday_calendar/ — one file per country
(cn.json), dates grouped by year, loaded here at import time (static
reference data, sibling of the label_dim tree; same JSON-as-source-of-
truth pattern as _common.request_config over external_request_config/).
The CN table covers 2020-01-01 through 2026-12-31; update the JSON each
January when the State Council publishes the next year's schedule (see
the file's notes for the market-vs-State-Council differences and the
2026-09-10 verification against real sessions).

Previously these lived in ``_download_commons.py``; they were migrated here
so that both build scripts (which import ``_db_commons`` only) and download
scripts can share the same calendar without pulling in HTTP/session helpers.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
_HOLIDAY_CALENDAR_DIR = (
    _REPO_ROOT / "database" / "sql" / "static" / "holiday_calendar"
)


def _load_country_calendar(country_code: str) -> Tuple[Set[date], Set[date]]:
    """Load (holidays, adjusted_workdays) from holiday_calendar/<code>.json.

    One file per country; each file groups its dates by year
    (``{"holidays": {"2026": ["2026-01-01", ...]}, "adjusted_workdays": {}}``).

    Fails LOUDLY on any problem. Unlike _common.request_config's
    skip-broken-field contract (a bad pacing value must not block a
    pipeline), a silently empty calendar would flip holidays into trading
    days across every pipeline — wrong results, not just wrong pacing —
    so a crash at import time is the safe failure mode.

    Validation guards hand edits to the JSON: year keys are 4-digit
    strings, every date parses as YYYY-MM-DD, and each date's year equals
    its year key. All problems are collected and raised together.
    """
    path = _HOLIDAY_CALENDAR_DIR / f"{country_code}.json"
    try:
        payload: Dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"public holidays file unreadable: {path} ({exc}) — the "
            "trading-day calendar is load-bearing; fix the JSON before "
            "running pipelines"
        ) from exc
    problems: List[str] = []
    country = payload.get("country")
    if not (
        isinstance(country, str) and country.lower() == country_code.lower()
    ):
        problems.append(
            f'"country" field {country!r} does not match file name '
            f'"{country_code}.json"'
        )
    holidays: Set[date] = set()
    adjusted: Set[date] = set()
    for field, target in (("holidays", holidays), ("adjusted_workdays", adjusted)):
        by_year = payload.get(field)
        if not isinstance(by_year, dict):
            problems.append(f'"{field}" must be an object keyed by year')
            continue
        for year_key, entries in by_year.items():
            if not (
                isinstance(year_key, str)
                and len(year_key) == 4
                and year_key.isascii()
                and year_key.isdigit()
            ):
                problems.append(
                    f'"{field}" year key {year_key!r} is not a 4-digit year'
                )
                continue
            if not isinstance(entries, list):
                problems.append(f'"{field}"["{year_key}"] must be a list of dates')
                continue
            for entry in entries:
                if not isinstance(entry, str):
                    problems.append(
                        f'"{field}"["{year_key}"] entry {entry!r} is not a string'
                    )
                    continue
                try:
                    parsed = datetime.strptime(entry, "%Y-%m-%d").date()
                except ValueError:
                    problems.append(
                        f'"{field}"["{year_key}"] entry {entry!r} '
                        "is not a YYYY-MM-DD date"
                    )
                    continue
                if parsed.year != int(year_key):
                    problems.append(
                        f'"{field}"["{year_key}"] entry {entry} '
                        "is outside its year key"
                    )
                    continue
                target.add(parsed)
    if problems:
        raise RuntimeError(
            f"invalid public holidays file {path}:\n  " + "\n  ".join(problems)
        )
    return holidays, adjusted


# ---------------------------------------------------------------------------
# Chinese A-share market calendar
#
# The table is the JSON above; the verification history (2026-09-10 check
# against real stats.index_basic_stats sessions, the pre-holiday-eve
# closures included, the five removed false entries) lives in cn.json's
# notes field. CN_ADJUSTED_WORKDAYS is intentionally EMPTY — the A-share
# market NEVER opens on weekend 调休 workdays, so the labor calendar's
# make-up days are irrelevant to market data (kept as a set for API
# compatibility).
# ---------------------------------------------------------------------------
CN_HOLIDAYS: Set[date]
CN_ADJUSTED_WORKDAYS: Set[date]
CN_HOLIDAYS, CN_ADJUSTED_WORKDAYS = _load_country_calendar("cn")


# ---------------------------------------------------------------------------
# Trading-day predicates
# ---------------------------------------------------------------------------
def is_trading_day(d: date) -> bool:
    """Return True if the Chinese A-share markets are open on date ``d``.

    Holidays (CN_HOLIDAYS) are checked first, then the standard Mon-Fri
    weekday rule applies (CN_ADJUSTED_WORKDAYS is empty — the market never
    opens on weekend 调休 make-up days).
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


def expected_publish_session(
    now: Optional[datetime] = None, *, eod_hour: int = 17,
) -> date:
    """Newest trading session whose EOD data a close-quote source may
    have published — TIME-OF-DAY aware.

    At/after ``eod_hour`` local on a CN trading day the session has
    closed and the EOD exports start carrying the day's rows (CN close
    15:00; index providers publish from ~16:00-17:30) — expect TODAY so
    evening sweeps actually fetch it. Before that hour (morning runs)
    only the previous session may exist (overseas/HK rows land mid-day);
    weekends/holidays always expect the last completed session.

    Consumers gate on CONTENT (a code whose local records lack this
    session is merely re-fetched, bounded by the caller's retry
    backoff), so an expectation running ahead of publication is safe.

    Replaces the unconditional ``last_business_day(today - 1)`` in the
    index close-quote downloaders (csindex/BSE/cnindex), which gated the
    ~18:00-evening sweeps to the PREVIOUS session until midnight — the
    day's rows for CSI-sourced codes then landed only on the NEXT
    trading day's run (no weekend runs: a Friday gap waited until
    Monday), leaving the nightly builds a partial frontier
    (2026-10-17 study: 186 of 523 codes missing 2026-10-16).
    """
    if now is None:
        now = datetime.now()
    today = now.date()
    if is_trading_day(today) and now.hour >= eod_hour:
        return today
    return last_business_day(today - timedelta(days=1))


def next_business_day(ref: Optional[date] = None) -> date:
    """Return the next trading day on or after ``ref`` (default: today).

    Takes into account weekends, Chinese public holidays (CN_HOLIDAYS), and
    adjusted workdays (CN_ADJUSTED_WORKDAYS).
    """
    d = ref if ref is not None else date.today()
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def recent_trading_day_cutoff(n_trading_days: int, ref: Optional[date] = None) -> date:
    """Return the date ``n_trading_days`` trading days ago (inclusive),
    measured from the most recent trading day on or before ``ref`` (default today).

    The returned date is the Nth-most-recent trading day, so the closed
    interval [cutoff, last_business_day(ref)] contains exactly
    ``n_trading_days`` trading days. Used by analyze_* scripts as a
    pre-filter to skip delisted/suspended securities: a code whose latest
    data point is older than ``cutoff`` has had no activity in the recent
    trading window and is excluded from the analysis universe.

    Examples (assuming ``ref`` is itself a trading day):
      n=1  → cutoff == ref              (just the most recent trading day)
      n=22 → cutoff is the 22nd-most-recent trading day (≈ one trading month)
    """
    if n_trading_days < 1:
        raise ValueError("n_trading_days must be >= 1")
    end = last_business_day(ref)
    cur = end
    count = 1  # `end` is the 1st trading day in the window
    while count < n_trading_days:
        cur -= timedelta(days=1)
        if is_trading_day(cur):
            count += 1
    return cur


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
