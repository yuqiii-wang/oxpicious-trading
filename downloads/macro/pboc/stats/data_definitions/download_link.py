"""DownloadLink — data definition.

Migrated verbatim from :mod:`downloads.macro.pboc.stats._scraper` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
from .stats_item import StatsItem


@dataclass
class DownloadLink:
    """A single xls download link found on a sub-page."""
    item: StatsItem
    year: int
    href: str
    row_text: str
    link_text: str
    # Year/month extracted from attachDir URL (publication date)
    pub_year: Optional[int] = None
    pub_month: Optional[int] = None
    ext: str = "xlsx"  # "xlsx" or "xls"

    def filename(self) -> str:
        """Generate output filename: pboc_stats_{slug}_{year}.{ext}"""
        return f"pboc_stats_{self.item.slug}_{self.year}.{self.ext}"

    def manifest_row(self) -> Dict[str, str]:
        """Return a dict suitable for CSV manifest output."""
        return {
            "year": str(self.year),
            "item_slug": self.item.slug,
            "cn_label": self.item.cn_label,
            "en_label": self.item.en_label,
            "page": self.item.page,
            "source_url": self.href,
            "pub_year": str(self.pub_year) if self.pub_year else "",
            "pub_month": str(self.pub_month) if self.pub_month else "",
            "filename": self.filename(),
            "row_text": self.row_text[:200],
        }
