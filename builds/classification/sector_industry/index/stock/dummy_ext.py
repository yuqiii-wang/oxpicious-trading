"""Dummy index creation for stocks — extends the ETF-only dummy.py pattern.

Stocks that have no qualifying industry index (weight > 2% threshold) but
match a stock_overrides name pattern get assigned a synthetic DUMMY parent
index so they appear in the UI hierarchy with a non-empty parent_index_code.

Dummy index properties (same convention as ETF dummies in dummy.py):
  * code: DUMMY_{industry_id} (e.g. DUMMY_EXPRESSWAY, DUMMY_PAPER)
  * name: {industry_label}综合指数 (e.g. 高速公路综合指数)
  * type: 'index'
  * is_dummy: True
  * parent_index_code: '' (root of hierarchy, like all indices)
  * sector_id/industry_id/is_industry_not_strategy: inherited from the industry

This module ensures a DUMMY index exists in the ``indices`` dict for any
industry that stock_overrides references.  It is called from classify_stocks
when a stock matches a stock_overrides pattern.
"""
from __future__ import annotations

from typing import Any, Dict

from _common.sec_statics.classification import (
    DEFAULT_SECTOR_ID,
    DEFAULT_SECTOR_LABEL,
    DEFAULT_INDUSTRY_ID,
    DEFAULT_INDUSTRY_LABEL,
)

DUMMY_PREFIX = "DUMMY_"


def dummy_code(industry_id: str) -> str:
    """Return the dummy index code for an industry_id."""
    return f"{DUMMY_PREFIX}{industry_id}"


def ensure_dummy_index(
    indices: Dict[str, Any],
    catalog: Dict[str, Any],
    sector_id: str,
    industry_id: str,
) -> str:
    """Ensure a DUMMY_{industry_id} index exists in the indices dict.

    If the dummy already exists (created by ETF dummy.py or a previous stock),
    returns its code without modification.  Otherwise creates a new dummy
    index entry with the industry's labels looked up from the catalog.

    Returns the dummy index code (DUMMY_{industry_id}).
    """
    dcode = dummy_code(industry_id)
    if dcode in indices:
        return dcode

    # Look up labels from the catalog.
    sector = catalog.get(sector_id)
    if sector is not None:
        sector_label = sector["label"]
        industry = sector["industries"].get(industry_id)
        if industry is not None:
            industry_label = industry["label"]
        else:
            industry_label = DEFAULT_INDUSTRY_LABEL
    else:
        sector_label = DEFAULT_SECTOR_LABEL
        industry_label = DEFAULT_INDUSTRY_LABEL

    indices[dcode] = {
        "name": f"{industry_label}综合指数",
        "exchange": None,
        "sector_id": sector_id,
        "industry_id": industry_id,
        "tags": [{"sector_id": sector_id, "industry_id": industry_id}],
        "is_industry_not_strategy": True,  # stock dummies are always industry-primary
        "is_dummy": True,
        "n_days": 0,
        "first_date": None,
        "last_date": None,
    }
    return dcode


__all__ = [
    "DUMMY_PREFIX",
    "dummy_code",
    "ensure_dummy_index",
]
