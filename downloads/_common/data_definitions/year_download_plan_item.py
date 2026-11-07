"""YearDownloadPlanItem — data definition.

Migrated verbatim from :mod:`downloads._common.plans` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class YearDownloadPlanItem:
    type_key: str
    prefix: str
    year: int
