"""Industry dummy indices — synthetic parent indices for orphan ETFs.

ETFs without a CSV-mapped tracking index (parent_index_code='') are "orphans".
This module creates one synthetic DUMMY index per industry_id to serve as
their parent, so every ETF has a non-empty parent_index_code.

Dummy index properties:
  * code: DUMMY_{industry_id} (e.g. DUMMY_BANKS, DUMMY_SEMI, DUMMY_OTHER)
  * name: {industry_label}综合指数 (e.g. 银行综合指数)
  * type: 'index'
  * is_dummy: True (real indices/ETFs/stocks have is_dummy=False)
  * parent_index_code: '' (root of hierarchy, like all indices)
  * sector_id/industry_id/is_industry_not_strategy: inherited from the industry

Dummy indices are added to the ``indices`` dict so they get upserted to
sec_classification + sec_index_tags like real indices.  They are filtered
out by json_io.save_json so they are NOT persisted to the JSON cache —
they are regenerated each build.
"""
from __future__ import annotations

from typing import Any, Dict

from _common.sec_statics.classification import (
    DEFAULT_SECTOR_ID,
    DEFAULT_SECTOR_LABEL,
    DEFAULT_INDUSTRY_ID,
    DEFAULT_INDUSTRY_LABEL,
)

# Prefix for dummy index codes.
DUMMY_PREFIX = "DUMMY_"


def _dummy_code(industry_id: str) -> str:
    """Return the dummy index code for an industry_id."""
    return f"{DUMMY_PREFIX}{industry_id}"


def create_dummy_indices(
    etfs: Dict[str, Any],
    indices: Dict[str, Any],
    catalog: Dict[str, Any],
    verbose: bool = True,
) -> Dict[str, Any]:
    """Create dummy indices for orphan ETFs and map them as parents.

    Scans ``etfs`` for entries with empty parent_index_code, groups them by
    their (sector_id, industry_id, is_industry_not_strategy), and creates
    one DUMMY index per group.  Orphan ETFs' parent_index_code is then set
    to the dummy code.

    Dummy indices are added to the ``indices`` dict (merged with real
    indices) so they get upserted to the DB.  They carry is_dummy=True so
    json_io.save_json can filter them out.

    Returns the merged ``indices`` dict.
    """
    # Collect orphan ETFs grouped by their industry.
    # Key: (sector_id, industry_id, is_industry_not_strategy)
    # Value: list of etf codes
    orphans_by_industry: Dict[tuple, list] = {}
    for etf_code, v in etfs.items():
        if v.get("parent_index_code", ""):
            continue  # has a real parent — skip
        sector_id = v.get("sector_id", DEFAULT_SECTOR_ID)
        industry_id = v.get("industry_id", DEFAULT_INDUSTRY_ID)
        is_ind = v.get("is_industry_not_strategy", True)
        key = (sector_id, industry_id, is_ind)
        orphans_by_industry.setdefault(key, []).append(etf_code)

    if not orphans_by_industry:
        if verbose:
            print(f"    [DUMMY] No orphan ETFs — 0 dummy indices created",
                  flush=True)
        return indices

    # Create a dummy index for each industry group.
    n_dummies = 0
    n_mapped = 0
    for (sector_id, industry_id, is_ind), etf_codes in orphans_by_industry.items():
        dummy_code = _dummy_code(industry_id)

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

        # Create the dummy index entry (same shape as real indices).
        indices[dummy_code] = {
            "name": f"{industry_label}综合指数",
            "exchange": None,
            "sector_id": sector_id,
            "industry_id": industry_id,
            "tags": [{"sector_id": sector_id, "industry_id": industry_id}],
            "is_industry_not_strategy": is_ind,
            "is_dummy": True,
            "n_days": 0,
            "first_date": None,
            "last_date": None,
        }
        n_dummies += 1

        # Map orphan ETFs to the dummy index.
        for etf_code in etf_codes:
            etfs[etf_code]["parent_index_code"] = dummy_code
            n_mapped += 1

    if verbose:
        print(f"    [DUMMY] {n_dummies} dummy indices created, "
              f"{n_mapped} orphan ETFs mapped", flush=True)

    return indices
