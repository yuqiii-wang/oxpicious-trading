"""llm_agents.online_search_summary.core.date_scan — the regex date-like
object scanner.

Picks DATE-LIKE OBJECTS out of free text (a search hit's title + snippet,
an article body): Chinese calendar forms (2026年11月7日 / 2026年11月 /
2026年), slash/hyphen ISO forms (2026-11-07 / 2026/11/7 / 2026-11) and
guarded bare years (2023) — the whole-text freshness signal behind the
compose seam's stale-source double check: when a reference's ENTIRE text
only talks about years older than the run's anchor year (the qa_id=22
failure: the scan finds 2023 but never 2026), the summarize prompt gets
a per-ref caution block instead of trusting the undated snippet.

Pure regex + dataclasses — no network, no DB, no provider imports.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DateScan:
    """The date-like objects found in one text run.

    ``years`` are the UNIQUE calendar years every matched form implies
    (sorted ascending); ``dates`` the valid full Y-M-D matches. A text
    with no date-like object at all has both empty — nothing says it is
    old, so it is never treated as stale.
    """

    years: List[int] = field(default_factory=list)
    dates: List[datetime.date] = field(default_factory=list)

    @property
    def newest_year(self) -> Optional[int]:
        """The newest year mentioned, or None when nothing matched."""
        return self.years[-1] if self.years else None

    def is_all_older(self, anchor: datetime.date) -> bool:
        """True when the text DOES talk about dates but ONLY about years
        older than *anchor*'s year — "scan only found 2023, but no 2026".
        Empty scans (undated text) are False: absence of dates is not
        evidence of staleness (the hit_ref_time NULL policy).
        """
        newest = self.newest_year
        return newest is not None and newest < anchor.year

    def sample_years(self, n: int = 3) -> List[int]:
        """A compact newest-first sample for the caution block."""
        return list(reversed(self.years[-n:]))


class DateScanner:
    """Regex scanner over free text -> :class:`DateScan`.

    False-positive direction is deliberately SAFE: a spurious year match
    (a 2099 price, an id) only RAISES ``newest_year``, which makes the
    all-older verdict LESS likely — the stale flag under-fires rather
    than over-fires, matching its role as a prompt caution, not a drop.
    """

    # Chinese calendar forms: 2026年11月7日 / 2026年11月 / 2026年. The
    # month/day groups are optional; 年 alone still pins a year.
    _CN_RE = re.compile(r"(20\d{2})年(?:(\d{1,2})月(?:(\d{1,2})日?)?)?")
    # Slash/hyphen forms: 2026-11-07 / 2026/11/7 / 2026-11 (year-month).
    # Day optional; month/day validity checked on the match (rejects the
    # 2023-20 of a 2023-2025 year RANGE — month 20 is not a month).
    _ISO_RE = re.compile(
        r"(?<!\d)(20\d{2})([-/])(\d{1,2})(?:\2(\d{1,2}))?(?!\d)")
    # Bare guarded year: any 20xx not glued to another digit (2023年,
    # "2023-2025" both ends, "2026点"). Catches everything the shaped
    # forms miss at the cost of the safe-direction noise above.
    _BARE_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

    def scan(self, text: Optional[str]) -> DateScan:
        """Collect every date-like object in *text* (empty scan on
        None/blank)."""
        s = text or ""
        years: set[int] = set()
        dates: List[datetime.date] = []
        for m in self._CN_RE.finditer(s):
            year = int(m.group(1))
            years.add(year)
            d = self._valid_date(m.group(1), m.group(2), m.group(3))
            if d:
                dates.append(d)
        for m in self._ISO_RE.finditer(s):
            year = int(m.group(1))
            years.add(year)
            d = self._valid_date(m.group(1), m.group(3), m.group(4))
            if d:
                dates.append(d)
        for m in self._BARE_YEAR_RE.finditer(s):
            years.add(int(m.group(1)))
        return DateScan(years=sorted(years),
                        dates=sorted(set(dates)))

    @staticmethod
    def _valid_date(y: Optional[str], m: Optional[str],
                    d: Optional[str]) -> Optional[datetime.date]:
        """A full Y-M-D match -> date; anything partial/invalid -> None
        (the year still counts via the years set)."""
        if not (y and m and d):
            return None
        try:
            return datetime.date(int(y), int(m), int(d))
        except ValueError:
            return None
