"""PeriodGrid — the YEAR period grid for analysis.signal_rules_audit.

Periods are CALENDAR YEARS keyed by the `period` SMALLINT (e.g. 2020).
Counts are additive across years (year buckets are disjoint), so no
all-history rows exist — the "All history" view SUM-re-aggregates in
the API SQL. All arithmetic is plain python ints on years (the frame
side buckets a year with one ``.dt.year``).

Target selection per run (mirrors the news_audit window contract):
  - a COMPLETE year computes once and is never recomputed, EXCEPT
    within the grace period (the most recent --grace-years complete
    years recompute to absorb late history_signals writes);
  - the TRAILING current year (the max event-bearing year) recomputes
    on every run;
  - ``force=True`` targets every event-bearing year;
  - years with NO events never materialize rows and count as covered
    (the caller unions the event-bearing year set into the covered
    set), and years present in the tables but no longer event-bearing
    are PURGE-ONLY targets (stale rows from a shrunken history).
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


def period_bounds(period: int) -> tuple[date, date]:
    """The (Jan 1, Dec 31) date bounds of a calendar-year period."""
    return date(period, 1, 1), date(period, 12, 31)


class PeriodGrid(BaseModel):
    """The year grid over one run's event-bearing span.

    Fields:
      event_years: calendar years with at least one standalone trading
        signal event at the run's sec_type (the signal_years view).
      grace_years: recompute slack over the most recent COMPLETE years.
      force: target every event-bearing year (full rebuild).
    """

    event_years: frozenset[int] = Field(default=frozenset())
    grace_years: int = 0
    force: bool = False

    @property
    def current_year(self) -> int:
        """The trailing (max) event-bearing year — partial, recomputes
        on every run."""
        return max(self.event_years)

    def all_years(self) -> list[int]:
        """Every event-bearing year, ascending."""
        return sorted(self.event_years)

    def targets(self, existing_years: set[int]) -> list[int]:
        """Event-bearing years this run must (re)compute, ascending.

        ``existing_years`` is the covered set: period values already
        materialized in the audit tables (empty under force — the run
        purges the scope). Non-event-bearing years are covered by
        construction: the caller intersects with the event set.
        """
        if self.force or not existing_years:
            return self.all_years()

        grace_floor = self.current_year - self.grace_years
        picked: list[int] = []
        for year in self.all_years():
            if year not in existing_years:
                picked.append(year)        # missing (complete or not)
            elif year >= grace_floor:
                picked.append(year)        # grace / trailing recompute
            elif year == self.current_year:
                picked.append(year)        # trailing year, always fresh
        return picked
