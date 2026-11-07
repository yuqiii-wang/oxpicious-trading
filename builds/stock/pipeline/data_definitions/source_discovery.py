"""SourceDiscovery — data definition.

Migrated verbatim from :mod:`builds.stock.pipeline.discovery` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class SourceDiscovery:
    """All discovered source files plus their loadable (non-holiday) dates."""
    all_files: list[tuple[str, str, str]]
    available_dates: set[date]
    loadable_dates: set[date]
    szse_margin_files: list[str]
    sse_margin_files: list[str]
