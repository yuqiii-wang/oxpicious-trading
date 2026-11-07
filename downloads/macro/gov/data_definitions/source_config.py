"""SourceConfig — data definition.

Migrated verbatim from :mod:`downloads.macro.gov.main_gov` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class SourceConfig:
    """Describes a gov-family title-list source.

    Attributes:
        name: short identifier used in logs and the summary (e.g. ``gov_news``).
        list_url: human-readable list page URL (used as Referer / in logs).
        out_dirname: sub-directory under ``temps/`` for this source's output.
        csv_filename: name of the combined titles CSV file.
        csv_columns: ordered CSV column names.
        raw_min_bytes: minimum bytes for a cached raw-JSON snapshot to be
            considered valid (sources may override for very small lists).
    """
    name: str
    list_url: str
    out_dirname: str
    csv_filename: str
    csv_columns: List[str] = field(default_factory=lambda: ["pub_date", "title", "url"])
    raw_min_bytes: int = 1000
