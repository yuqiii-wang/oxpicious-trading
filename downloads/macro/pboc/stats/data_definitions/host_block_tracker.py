"""HostBlockTracker — data definition.

Migrated verbatim from :mod:`downloads.macro.pboc.stats._session` (one file per ``@dataclass``)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict
from urllib.parse import urlparse


@dataclass
class HostBlockTracker:
    """Track hosts that return 4xx to avoid hammering blocked endpoints."""
    _blocked: Dict[str, float] = field(default_factory=dict)  # host -> block_time
    _block_duration: float = 3600.0  # 1 hour cooldown

    def record_error(self, url: str, status_code: int) -> None:
        host = urlparse(url).netloc
        self._blocked[host] = time.time()

    def is_blocked(self, url: str) -> bool:
        host = urlparse(url).netloc
        if host not in self._blocked:
            return False
        if time.time() - self._blocked[host] > self._block_duration:
            del self._blocked[host]
            return False
        return True
