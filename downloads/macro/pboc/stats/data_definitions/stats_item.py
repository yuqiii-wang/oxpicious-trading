"""StatsItem — data definition.

Migrated verbatim from :mod:`downloads.macro.pboc.stats._catalog` (one file per ``@dataclass``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class StatsItem:
    """A target statistical item to download from PBoC."""
    slug: str                  # e.g. "afre_flow" — used in filenames
    page: str                  # "shrzgm" or "hbtjgl"
    cn_label: str              # e.g. "社会融资规模增量统计表"
    en_label: str              # e.g. "Aggregate Financing to the Real Economy (Flow)"
    match_patterns: List[str]  # regex patterns matched against row text
    description: str

    def matches(self, row_text: str) -> bool:
        """Return True if *row_text* matches any of this item's patterns."""
        for pat in self.match_patterns:
            if re.search(pat, row_text, re.IGNORECASE):
                return True
        return False
