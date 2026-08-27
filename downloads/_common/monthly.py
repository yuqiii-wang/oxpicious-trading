"""_download_commons_monthly.py — Shared month-start refresh logic.

Provides helpers used by all composition download scripts to implement the
"month-start refresh" pattern: on the 1st of each month, bypass the cache
and stamp output files with today's date so a fresh monthly snapshot flows
to prod (stats.sec_composition) under the new month's date.

Typical usage in a download script::

    from _download_commons_monthly import is_month_start

    today = date.today()
    month_start = force_month_start or is_month_start(today)
    if month_start:
        skip_cached = False
        # ... stamp output with today's date ...
"""
from __future__ import annotations

from datetime import date, timedelta

from downloads._common import is_trading_day


def is_month_start(d: date) -> bool:
    """Return True if *d* is the 1st day of its month (month-start trigger)."""
    return d.day == 1


def most_recent_trading_day(d: date) -> date:
    """Return *d* if it is a trading day, else the most recent trading day on or before *d*.

    Used by backfill-style downloaders (e.g. ``download_szse_etf_composition``)
    that need to query an API for a real trading day but stamp the output with
    today's date when today happens to be a non-trading day (weekend/holiday).
    """
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d
