"""RunStats — data definition.

Migrated verbatim from :mod:`downloads._common.plans` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class RunStats:
    downloaded: int = 0
    skipped_cached: int = 0
    failed: int = 0
    empty: int = 0
    files: List[str] = field(default_factory=list)

    def to_dict(self, **extra: Any) -> Dict[str, Any]:
        d = {
            "downloaded": self.downloaded,
            "skipped_cached": self.skipped_cached,
            "failed": self.failed,
            "empty": self.empty,
            "files": list(self.files),
        }
        d.update(extra)
        return d
