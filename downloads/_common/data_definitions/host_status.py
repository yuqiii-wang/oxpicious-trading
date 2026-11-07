"""HostStatus — data definition.

Migrated verbatim from :mod:`downloads._common.net` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HostStatus:
    blocked: bool = False
    blocked_reason: str = ""
    last_error_time: float = 0.0
    error_count: int = 0
