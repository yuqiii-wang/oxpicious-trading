"""ChunkDownloadPlanItem — data definition.

Migrated verbatim from :mod:`downloads._common.plans` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class ChunkDownloadPlanItem:
    type_key: str
    prefix: str
    chunk_start: date
    chunk_end: date
