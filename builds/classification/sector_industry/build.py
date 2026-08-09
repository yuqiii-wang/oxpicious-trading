"""Classification-state orchestrator.

Assembles the full classification state by composing the three leaves in
hierarchy order:

    sector_industry  (catalog + owner matchers)
          ↑
        index        ← classify_indices
          ↑
      etf / stock    ← classify_etfs / classify_stocks (inherit from index)

Index classifications are loaded from the JSON cache; any new indices are
classified by keyword rules.  ETF and stock mappings are always recomputed
each run (ETFs from CSV + index inheritance; stocks from DB sec_composition).

Returns a dict with keys: catalog, indices, etfs, stocks, owners, built_at,
csv_source.  The JSON persistence (indices + catalog only) and DB upsert are
handled by ``json_io.save_json`` and ``upsert.upsert_to_db`` respectively.
"""
from __future__ import annotations

import datetime
import os
from typing import Any, Dict, List, Optional

from _common.build_commons import PROJECT_ROOT
from _common.sec_statics.classification import DEFAULT_SECTOR_ID

from builds.classification.sector_industry.catalog import build_catalog
from builds.classification.sector_industry.csv_loader import find_latest_csv
from builds.classification.sector_industry.owners import build_owner_matchers
from builds.classification.sector_industry.index.classify import classify_indices
from builds.classification.sector_industry.index.etf.classify import classify_etfs
from builds.classification.sector_industry.index.etf.unmatched import (
    fetch_unmatched_funds, classify_unmatched_funds)
from builds.classification.sector_industry.index.etf.sec_info_fallback import (
    fetch_sec_info_names, reclassify_other_etfs)
from builds.classification.sector_industry.index.dummy import create_dummy_indices
from builds.classification.sector_industry.index.stock.classify import classify_stocks


async def build_classification(
    conn,
    etf_rows: List[Dict[str, Any]],
    prev_state: Optional[Dict[str, Any]] = None,
    owners: Optional[List[Dict[str, Any]]] = None,
    verbose: bool = True,
    reclassify_indices: bool = False,
) -> Dict[str, Any]:
    """Build the full classification state from JSON (indices) + CSV/DB (ETFs, stocks).

    Index classifications are loaded from ``prev_state`` (the JSON cache);
    any new indices not in the JSON are classified by keyword rules.
    ETF and stock mappings are always recomputed.

    ``reclassify_indices`` — when True, ignores the JSON-cached sector_id /
    industry_id / tags for all indices found in CSV/DB and reclassifies them
    from keyword rules.  Manually-added indices (in JSON but not in CSV/DB)
    are always preserved as-is.  Use this after changing INDEX_RULES to
    propagate the new rules to existing indices.

    ``owners`` is the list from sec_owners.json; when provided, each ETF is
    matched to an owner_id (via CSV `管理人` full-name or ETF-name alias match)
    and parent_index_is_primary is computed for ETFs and stocks.

    Returns a dict with keys: catalog, indices, etfs, stocks, owners, built_at, csv_source.
    """
    catalog = build_catalog()
    prev_indices: Dict[str, Any] = (prev_state or {}).get("indices", {})

    # Owner matchers (empty if sec_owners.json missing — owner_id stays NULL).
    full_name_to_id, alias_pairs = build_owner_matchers(owners or [])

    # --- 1. Classify indices (root of the hierarchy under sector_industry) ---
    indices = await classify_indices(
        conn, etf_rows, prev_indices, reclassify_indices, verbose)

    # --- 2. Map ETFs to indices (inherit classification) ---
    etfs = classify_etfs(
        etf_rows, indices, full_name_to_id, alias_pairs, verbose)

    # --- 2b. Classify unmatched exchange-traded funds from v_etf_margin ---
    # These are funds (structured/LOF/active) NOT in the ETF→index CSV but
    # present in the etf-margin view.  Classified by name so they don't show
    # as '未分类' on the UI.
    all_funds = await fetch_unmatched_funds(conn)
    etfs = classify_unmatched_funds(all_funds, etfs, verbose)

    # --- 2b.5. Re-classify OTHER ETFs using the official sec_info.name ---
    # ETFs whose CSV/v_etf_margin name was an opaque trading abbreviation
    # (e.g. '瑞和远见', '保证金') may still be at OTHER.  stats.sec_info
    # stores the OFFICIAL fund name (基金简称) from SZSE quarterly reports,
    # which embeds the tracking index/theme verbatim — retry classification
    # with it.  Runs BEFORE create_dummy_indices so re-classified ETFs join
    # their proper industry's dummy group instead of DUMMY_OTHER.
    other_codes = [c for c, v in etfs.items()
                   if v.get("sector_id", DEFAULT_SECTOR_ID) == DEFAULT_SECTOR_ID]
    if other_codes:
        sec_info_names = await fetch_sec_info_names(conn, other_codes)
        reclassify_other_etfs(etfs, sec_info_names, verbose)

    # --- 2c. Create industry dummy indices for orphan ETFs ---
    # ETFs with no parent_index_code (not in CSV, or unmatched funds) get
    # mapped to a synthetic DUMMY_{industry_id} index so every ETF has a
    # non-empty parent.  Dummy indices are NOT persisted to JSON.
    indices = create_dummy_indices(etfs, indices, catalog, verbose)

    # --- 3. Map stocks to indices (all qualifying, weight > 2%, excl. strategy-only) ---
    stocks = await classify_stocks(conn, indices, verbose)

    return {
        "version": 1,
        "built_at": datetime.datetime.now().isoformat(),
        "csv_source": os.path.relpath(find_latest_csv() or "", PROJECT_ROOT),
        "catalog": catalog,
        "indices": indices,
        "etfs": etfs,
        "stocks": stocks,
        "owners": owners or [],
    }
