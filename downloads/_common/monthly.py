"""_download_commons_monthly.py — Shared month/quarter-start refresh logic.

Provides helpers used by the composition download scripts to implement the
"start-of-period refresh" pattern: on the 1st of the period's first month,
bypass the cache and stamp output files with today's date so a fresh
snapshot flows to prod (stats.sec_composition) under the new period's
date. Two period vocabularies share the helpers:

  * quarter-start (is_quarter_start) — the composition tasks' quarterly
    cadence (ALL of them since 2026-11, when the four index composition /
    linked-ETF tasks moved off the monthly tier into downloads_quarterly
    alongside the ETF composition tasks): refresh inside the
    QUARTER-START window — a quarter-start month (Jan/Apr/Jul/Oct) up to
    ``QUARTER_REFRESH_LAST_DAY``. The orchestration arrangement fires the
    quarterly tier ONE WEEK after the calendar quarter start (a
    2027-Jan-01 quarter comes due 2027-Jan-08) and the first trading day
    on or after that boundary can slide on a holiday, so the guard
    accepts the window instead of the exact 1st.
  * month-start (is_month_start) — refresh on the 1st of EVERY month;
    retained for month-cadence download tasks (no composition task uses
    it since the 2026-11 quarterly migration).

Typical usage in a download script::

    from downloads._common.monthly import is_quarter_start

    today = date.today()
    quarter_start = force_quarter_start or is_quarter_start(today)
    if quarter_start:
        skip_cached = False
        # ... stamp output with today's date ...
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from downloads._common import is_trading_day


QUARTER_MONTHS: tuple[int, ...] = (1, 4, 7, 10)
"""The quarter-start months (the composition tasks' quarterly refresh
months — one snapshot per index/ETF per calendar quarter, taken at the
start)."""

QUARTER_REFRESH_LAST_DAY: int = 15
"""The last day of the quarter-start refresh window. The quarterly tier
fires one week after the calendar quarter start (the arrangement's
quarterly boundary: a 2027-Jan-01 quarter comes due 2027-Jan-08) and a
holiday can slide the first trading day past that boundary, so the
tasks' quarter-start guard accepts the whole first half of the
quarter-start month rather than the exact 1st."""


def is_month_start(d: date) -> bool:
    """Return True if *d* is the 1st day of its month (month-start trigger)."""
    return d.day == 1


def is_quarter_start(d: date) -> bool:
    """Return True if *d* falls in the quarter-start refresh window — a
    quarter-start month (Jan/Apr/Jul/Oct) up to
    ``QUARTER_REFRESH_LAST_DAY`` (the quarterly refresh trigger)."""
    return d.month in QUARTER_MONTHS and d.day <= QUARTER_REFRESH_LAST_DAY


def in_same_quarter(a: date, b: date) -> bool:
    """Return True if *a* and *b* fall in the same calendar quarter."""
    return a.year == b.year and (a.month - 1) // 3 == (b.month - 1) // 3


def current_quarter_csv(directory: Path, pattern: str,
                        today: date) -> Path | None:
    """The latest CSV under *directory* matching *pattern* whose embedded
    ``_YYYYMMDD`` date falls in *today*'s calendar quarter, or None.

    The quarterly cadence's skip check for per-run-dated outputs (the
    intro tasks): a current-quarter snapshot already on disk means the
    run is a no-op — the first run of a quarter fetches, later runs in
    the same quarter skip, whenever they happen (a quarter-start holiday
    delays the refresh to the first trading day, it never skips it).
    """
    best: tuple[date, Path] | None = None
    for f in directory.glob(pattern):
        m = re.search(r"_(\d{8})\.csv$", f.name)
        if not m:
            continue
        raw = m.group(1)
        d = date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
        if in_same_quarter(d, today) and (best is None or d > best[0]):
            best = (d, f)
    return best[1] if best else None


def most_recent_trading_day(d: date) -> date:
    """Return *d* if it is a trading day, else the most recent trading day on or before *d*.

    Used by backfill-style downloaders (e.g. ``download_szse_etf_composition``)
    that need to query an API for a real trading day but stamp the output with
    today's date when today happens to be a non-trading day (weekend/holiday).
    """
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d
