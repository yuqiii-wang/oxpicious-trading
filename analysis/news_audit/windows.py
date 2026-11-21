"""AuditWindowGrid — the 5-calendar-day window grid for analysis.news_audit.

Windows are non-overlapping 5-day tiles anchored at config.ANCHOR_DATE
(2000-01-03): a date ``d`` belongs to the window whose start is
``ANCHOR_DATE + floor((d - ANCHOR_DATE) / 5) * 5`` days. All arithmetic runs
on integer day-numbers (days since the unix epoch) so the grid is a pure
int64 space — the frame side bucket a window with one floordiv + multiply.

Target selection per run (mirrors the analysis_composites stride contract):
  - a COMPLETE window (window_end <= latest news date) computes once and is
    never recomputed, EXCEPT within the grace period (recent windows
    recompute to absorb late digestions / keyword rewrites);
  - the TRAILING partial window (the one containing the latest news date)
    recomputes on every run until its 5 days are past;
  - ``force=True`` targets every window from the first news date on.
"""
from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, Field

from analysis.news_audit.config import ANCHOR_DAYS, WINDOW_DAYS


class AuditWindow(BaseModel):
    """One grid window as a typed payload (crosses into the step layer)."""

    window_start: date
    window_end: date
    is_complete: bool

    @property
    def start_days(self) -> int:
        """Window start as days since the unix epoch (the grid integer)."""
        return (self.window_start - date(1970, 1, 1)).days


class AuditWindowGrid(BaseModel):
    """The stride-5 calendar-day grid over one run's source span.

    Fields:
      first_days / latest_days: day-numbers of MIN/MAX(text.news.date) —
        the source span this run may build windows over.
      grace_days: recompute slack for recently completed windows.
      force: target every window in the span (full rebuild).
    """

    window_days: int = WINDOW_DAYS
    anchor_days: int = ANCHOR_DAYS
    first_days: int
    latest_days: int
    grace_days: int = 0
    force: bool = False

    @property
    def stride(self) -> int:
        """Stride == window length: the tiles do not overlap."""
        return self.window_days

    def window_start_days(self, day_num: int) -> int:
        """Grid start (day-number) of the window containing ``day_num``."""
        return (
            self.anchor_days
            + (day_num - self.anchor_days) // self.stride * self.stride
        )

    def window(self, start_days: int) -> AuditWindow:
        """Typed window for a grid start (day-number)."""
        window_start = date(1970, 1, 1) + timedelta(days=start_days)
        window_end = date(1970, 1, 1) + timedelta(
            days=start_days + self.window_days - 1
        )
        return AuditWindow(
            window_start=window_start,
            window_end=window_end,
            is_complete=self._end_days(start_days) <= self.latest_days,
        )

    def _end_days(self, start_days: int) -> int:
        return start_days + self.window_days - 1

    def all_starts(self) -> list[int]:
        """Every grid start (day-number) over the source span, ascending."""
        first_start = self.window_start_days(self.first_days)
        last_start = self.window_start_days(self.latest_days)
        return list(range(first_start, last_start + 1, self.stride))

    def targets(self, existing_start_days: set[int]) -> list[int]:
        """Grid starts (day-numbers) this run must (re)compute, ascending.

        ``existing_start_days`` is the COVERED set: window_start values
        already materialized in the target table UNION the grid's
        news-empty windows (empty under force — the run purged it).
        """
        all_starts = self.all_starts()
        if self.force or not existing_start_days:
            return all_starts

        grace_floor = self.latest_days - self.grace_days
        trailing_start = self.window_start_days(self.latest_days)
        picked: list[int] = []
        for s in all_starts:
            if s not in existing_start_days:
                picked.append(s)          # missing (complete or not)
            elif self._end_days(s) >= grace_floor:
                picked.append(s)          # grace / trailing recompute
            elif s == trailing_start:
                picked.append(s)          # trailing window, always fresh
        return picked

    def load_from(self, target_start_days: list[int]) -> date | None:
        """Earliest source date to fetch for the targets (None = no work)."""
        if not target_start_days:
            return None
        return date(1970, 1, 1) + timedelta(days=min(target_start_days))
