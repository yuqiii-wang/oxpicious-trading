"""Industry dummy indices — synthetic parent indices for orphan ETFs.

ETFs without a CSV-mapped tracking index (parent_index_code='') are "orphans".
Before creating a dummy, this module tries to match each orphan ETF to a REAL
index by name containment — if the ETF name contains a real index name in the
same industry, the ETF is mapped to that real index instead.  Only orphans
that cannot be matched to any real index get a synthetic DUMMY parent.

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

from typing import Any, Dict, List, Tuple

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


def _build_real_index_name_lookup(
    indices: Dict[str, Any],
) -> Dict[Tuple[str, str, bool], List[Tuple[str, str]]]:
    """Build a lookup of real index names grouped by industry.

    Returns a dict keyed by (sector_id, industry_id, is_industry_not_strategy)
    → list of (index_code, index_name) for REAL indices (is_dummy != True)
    with a non-empty name.  Used to match orphan ETFs to real indices by name
    containment before falling back to a dummy.
    """
    lookup: Dict[Tuple[str, str, bool], List[Tuple[str, str]]] = {}
    for code, data in indices.items():
        if data.get("is_dummy"):
            continue  # skip existing dummies
        name = data.get("name", "")
        if not name:
            continue
        sector_id = data.get("sector_id", DEFAULT_SECTOR_ID)
        industry_id = data.get("industry_id", DEFAULT_INDUSTRY_ID)
        is_ind = data.get("is_industry_not_strategy", True)
        key = (sector_id, industry_id, is_ind)
        lookup.setdefault(key, []).append((code, name))
    # Sort each group by name length descending so the longest (most specific)
    # index name is matched first — e.g. "国证2000" before "国证".
    for names in lookup.values():
        names.sort(key=lambda x: len(x[1]), reverse=True)
    return lookup


def _match_orphan_to_real_index(
    etf_name: str,
    real_indices: List[Tuple[str, str]],
) -> str:
    """Try to match an orphan ETF to a real index by name containment.

    Returns the matched index code, or "" if no real index name is contained
    in the ETF name.  Longer index names are checked first (most specific
    match wins) to avoid "国证" matching before "国证2000".
    """
    if not etf_name or not real_indices:
        return ""
    for icode, iname in real_indices:
        if iname and iname in etf_name:
            return icode
    return ""


def create_dummy_indices(
    etfs: Dict[str, Any],
    indices: Dict[str, Any],
    catalog: Dict[str, Any],
    verbose: bool = True,
) -> Dict[str, Any]:
    """Create dummy indices for orphan ETFs and map them as parents.

    Scans ``etfs`` for entries with empty parent_index_code.  For each
    orphan, FIRST tries to match it to a REAL index in the same industry by
    name containment (e.g. an ETF named "国证2000ETF" matches real index
    399303 "国证2000").  Only orphans that cannot be matched to any real
    index are grouped by their (sector_id, industry_id, is_industry) and
    assigned a synthetic DUMMY_{industry_id} parent.

    Dummy indices are added to the ``indices`` dict (merged with real
    indices) so they get upserted to the DB.  They carry is_dummy=True so
    json_io.save_json can filter them out.

    Returns the merged ``indices`` dict.
    """
    # Build real-index name lookup grouped by industry for containment match.
    real_index_lookup = _build_real_index_name_lookup(indices)

    # First pass: try to match orphans to real indices by name containment.
    n_real_matched = 0
    remaining_orphans: List[str] = []
    for etf_code, v in etfs.items():
        if v.get("parent_index_code", ""):
            continue  # has a real parent — skip
        sector_id = v.get("sector_id", DEFAULT_SECTOR_ID)
        industry_id = v.get("industry_id", DEFAULT_INDUSTRY_ID)
        is_ind = v.get("is_industry_not_strategy", True)
        key = (sector_id, industry_id, is_ind)
        etf_name = v.get("name", "")

        # Try matching to a real index in the same industry.
        real_candidates = real_index_lookup.get(key, [])
        matched_code = _match_orphan_to_real_index(etf_name, real_candidates)
        if matched_code:
            v["parent_index_code"] = matched_code
            n_real_matched += 1
        else:
            remaining_orphans.append(etf_code)

    # Second pass: group remaining orphans by industry for dummy creation.
    orphans_by_industry: Dict[tuple, list] = {}
    for etf_code in remaining_orphans:
        v = etfs[etf_code]
        sector_id = v.get("sector_id", DEFAULT_SECTOR_ID)
        industry_id = v.get("industry_id", DEFAULT_INDUSTRY_ID)
        is_ind = v.get("is_industry_not_strategy", True)
        key = (sector_id, industry_id, is_ind)
        orphans_by_industry.setdefault(key, []).append(etf_code)

    n_dummies = 0
    n_dummy_mapped = 0
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
            n_dummy_mapped += 1

    if verbose:
        print(f"    [DUMMY] {n_real_matched} orphans matched to real indices, "
              f"{n_dummies} dummy indices created, "
              f"{n_dummy_mapped} orphan ETFs mapped to dummies", flush=True)

    return indices
