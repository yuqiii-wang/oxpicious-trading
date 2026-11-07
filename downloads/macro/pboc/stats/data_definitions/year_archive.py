"""YearArchive — data definition.

Migrated verbatim from :mod:`downloads.macro.pboc.stats._scraper` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class YearArchive:
    """A year's archive page URL discovered from the central index."""
    year: int
    index_url: str
