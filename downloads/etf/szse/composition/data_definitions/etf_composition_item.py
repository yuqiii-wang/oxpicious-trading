"""EtfCompositionItem — data definition.

Migrated verbatim from :mod:`downloads.etf.szse.composition.__main__` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class EtfCompositionItem:
    trade_date: date
    etf_code: str
    title: str
    detail_path: str
    detail_url: str
    md_filename: str
