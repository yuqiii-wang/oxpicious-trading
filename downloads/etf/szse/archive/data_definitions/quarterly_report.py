"""QuarterlyReport — data definition.

Migrated verbatim from :mod:`downloads.etf.szse.archive.reports` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class QuarterlyReport:
    """A single quarterly report announcement from the annList API."""
    etf_code: str        # bare 6-digit code, e.g. "160812"
    etf_name: str        # fund name, e.g. "长盛同益LOF"
    year: int            # report year, e.g. 2026
    quarter: int         # 1-4
    title: str           # full announcement title
    publish_date: str    # YYYY-MM-DD
    attach_path: str     # path under PDF_BASE_URL, e.g. "/download/..."
    attach_id: str       # announcement uuid (unique id)

    @property
    def tag(self) -> str:
        return f"{self.etf_code}_{self.year}Q{self.quarter}"

    @property
    def pdf_filename(self) -> str:
        return f"{self.tag}.pdf"
