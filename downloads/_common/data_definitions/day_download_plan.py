"""DayDownloadPlan — data definition.

Migrated verbatim from :mod:`downloads._common.plans` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class DayDownloadPlan:
    items: List[DayDownloadPlanItem] = field(default_factory=list)
    present_count: int = 0
    total_expected: int = 0

    def summary_str(self) -> str:
        return (
            f"expected={self.total_expected} cached={self.present_count} "
            f"missing_to_download={len(self.items)}"
        )
