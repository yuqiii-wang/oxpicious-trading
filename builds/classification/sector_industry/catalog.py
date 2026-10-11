"""Sector → industry catalog + label/date helpers.

The catalog is the parent concern of the whole classification tree: every
index, ETF and stock is ultimately classified into a (sector_id,
industry_id) pair that must exist in this catalog.  ``build_catalog`` now
lives in ``database.sec_statics.classification`` as a pure function of the
JSON rule configs (INDEX_RULES — which spans BOTH real industries and
strategy sectors — plus the overlapping_catalog entries from
classification_rules/config.json); this module re-exports it and keeps the
DB-layer helpers.

This module is intentionally leaf-safe: it imports only from
``database.sec_statics.classification`` (the rules engine) so the index/etf
/stock leaves and upsert modules can depend on it without creating
import cycles.

``_lookup_labels`` denormalizes (sector_label, industry_label,
industry_slug) onto every sec_classification row at upsert time, and
``_parse_date`` normalizes the loose date strings carried in the state
into ``datetime.date`` values for the DB.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional, Tuple

from database.sec_statics.classification import (
    DEFAULT_INDUSTRY_ID,
    DEFAULT_INDUSTRY_LABEL,
    DEFAULT_SECTOR_LABEL,
    OVERLAPPING_CATALOG,
    build_catalog,
)

__all__ = ["build_catalog", "OVERLAPPING_CATALOG", "_lookup_labels", "_parse_date"]


def _lookup_labels(
    catalog: Dict[str, Any],
    sector_id: str,
    industry_id: str,
) -> Tuple[str, str, str]:
    """Look up (sector_label, industry_label, industry_slug) from the catalog.

    Used to denormalize labels onto every sec_classification row at upsert
    time so frontend services can render labels without JOINing a catalog
    table.  Falls back to the DEFAULT_* constants when the sector_id or
    industry_id is not present in the catalog (e.g. legacy 'OTHER' entries).
    """
    sector = catalog.get(sector_id)
    if sector is None:
        return (DEFAULT_SECTOR_LABEL, DEFAULT_INDUSTRY_LABEL,
                DEFAULT_INDUSTRY_ID.lower())
    industry = sector["industries"].get(industry_id)
    if industry is None:
        return (sector["label"], DEFAULT_INDUSTRY_LABEL,
                DEFAULT_INDUSTRY_ID.lower())
    return (sector["label"], industry["label"], industry["slug"])


def _parse_date(s: Optional[str]) -> Optional[datetime.date]:
    """Parse a YYYY-MM-DD string to datetime.date (None if blank/invalid)."""
    if not s:
        return None
    try:
        return datetime.datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
