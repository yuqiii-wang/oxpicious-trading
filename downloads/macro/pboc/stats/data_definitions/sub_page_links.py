"""SubPageLinks — data definition.

Migrated verbatim from :mod:`downloads.macro.pboc.stats._scraper` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SubPageLinks:
    """Discovered sub-page URLs for a single year."""
    year: int
    shrzgm_url: Optional[str] = None
    hbtjgl_url: Optional[str] = None
