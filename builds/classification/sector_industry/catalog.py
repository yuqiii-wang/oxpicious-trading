"""Sector → industry catalog + label/date helpers.

The catalog is the parent concern of the whole classification tree: every
index, ETF and stock is ultimately classified into a (sector_id,
industry_id) pair that must exist in this catalog.  It is built from
``INDEX_RULES`` (which spans BOTH real industries and strategy sectors)
plus a small set of OVERLAPPING entries where the same industry_id lives
under multiple sector_ids.

This module is intentionally leaf-safe: it imports only from
``_common.sec_statics.classification`` (the rules engine) so the index/etf
/stock leaves and upsert modules can depend on it without creating
import cycles.

``_lookup_labels`` denormalizes (sector_label, industry_label,
industry_slug) onto every sec_classification row at upsert time, and
``_parse_date`` normalizes the loose date strings carried in the state
into ``datetime.date`` values for the DB.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

from _common.sec_statics.classification import (
    INDEX_RULES,
    DEFAULT_SECTOR_ID,
    DEFAULT_SECTOR_LABEL,
    DEFAULT_INDUSTRY_ID,
    DEFAULT_INDUSTRY_LABEL,
)

# Overlapping catalog entries: same industry_id under multiple sector_ids.
# These are added to the catalog even if no security is assigned to them,
# documenting that the industry conceptually spans multiple sectors.
OVERLAPPING_CATALOG: List[Tuple[str, str, str, str]] = [
    # (sector_id, sector_label, industry_id, industry_label)
    ("ENG", "能源", "POWER_EQUIP", "电力设备"),
    ("IND", "工业", "POWER_EQUIP", "电力设备"),
    ("NEV", "新能源", "PV", "光伏"),
    ("IND", "工业", "PV", "光伏"),
    ("NEV", "新能源", "BATTERY", "储能/电池"),
    ("IND", "工业", "BATTERY", "储能/电池"),
]


def build_catalog() -> Dict[str, Dict[str, Any]]:
    """Build the unified sector → industry catalog from INDEX_RULES + overlapping entries.

    INDEX_RULES = INDUSTRY_RULES + STRATEGY_RULES, so the catalog covers BOTH
    real industries (FIN, TECH, HC, ...) and strategy sectors (BROAD, DIV,
    REGION, STRATEGY, SOE).  A single catalog is sufficient because the DB
    stores strategy classifications in the same sector_id/industry_id columns
    (selected by is_industry_not_strategy) — there is no separate
    strategy/theme catalog.

    Returns:
        { sector_id: { "label": ..., "industries": { industry_id: { "label": ..., "slug": ... } } } }
    """
    catalog: Dict[str, Dict[str, Any]] = {}

    def _add(sector_id: str, sector_label: str, industry_id: str, industry_label: str):
        if sector_id not in catalog:
            catalog[sector_id] = {"label": sector_label, "industries": {}}
        catalog[sector_id]["label"] = sector_label
        if industry_id not in catalog[sector_id]["industries"]:
            catalog[sector_id]["industries"][industry_id] = {
                "label": industry_label,
                "slug": industry_id.lower(),
            }
        else:
            catalog[sector_id]["industries"][industry_id]["label"] = industry_label

    # Add OTHER default
    _add(DEFAULT_SECTOR_ID, DEFAULT_SECTOR_LABEL, DEFAULT_INDUSTRY_ID, DEFAULT_INDUSTRY_LABEL)

    for sector_id, sector_label, industry_id, industry_label, _ in INDEX_RULES:
        _add(sector_id, sector_label, industry_id, industry_label)

    for sector_id, sector_label, industry_id, industry_label in OVERLAPPING_CATALOG:
        _add(sector_id, sector_label, industry_id, industry_label)

    return catalog


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
