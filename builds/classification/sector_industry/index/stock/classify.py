"""Stock classification leaf — stocks classified to their qualifying indices.

Stocks sit one level below index (like ETFs) but are MANY-TO-MANY: each
qualifying index (weight > STOCK_WEIGHT_THRESHOLD, excluding strategy-primary
indices) produces ONE ROW, so a stock may have multiple rows in
sec_classification.  Stocks without any qualifying index fall through to a
name-based classification pipeline (see below).

Strategy-primary indices (is_industry_not_strategy=FALSE, e.g. 沪深300 →
BROAD, 中证红利 → DIV) are excluded because they convey no industry
information — the stock is assigned to the next qualifying index where
is_industry_not_strategy=TRUE instead.  Indices not in the ``indices``
dict (unclassified) are included — the caller can classify them via JSON
later.

Individual stocks are NEVER classified to a strategy sector.  Strategy
sectors (BROAD, DIV, REGION, STRATEGY, SOE) are designed for index/ETF
names (e.g. "沪深300", "中证红利"); matching them against stock company
names (e.g. "深天地Ａ"→深, "中国天楹"→中国) produces false positives.

--- Name-based fallback pipeline (when no qualifying index exists) ---

1. INDUSTRY_RULES keyword match (classify_index) — catches stock names
   containing industry keywords (e.g. "深圳能源" → ENG/COAL, "中环环保"
   → ESG/GREEN).  Uses the SAME keyword rules as index/ETF classification.

2. stock_overrides name-pattern map (match_stock_override) — catches
   stock-specific patterns not in INDUSTRY_RULES (e.g. "中原高速" →
   IND/EXPRESSWAY, "美好置业" → RE/RE_REAL_ESTATE).  Curated in
   builds/classification/sector_industry/stock_overrides/.

3. If neither matches, the stock stays at (OTHER, OTHER).

When either fallback matches, the stock gets a synthetic DUMMY_{industry_id}
parent index (e.g. DUMMY_EXPRESSWAY, DUMMY_PHARMA_BROAD) so it appears in
the UI hierarchy with a non-empty parent_index_code.  The dummy index is
created on-demand via ensure_dummy_index() and shares the same convention
as ETF dummies (DUMMY_ prefix, is_dummy=True, never persisted to JSON).

parent_index_is_primary: exactly ONE row per code — the one with
MAX(parent_index_weight); other rows tied at the same max weight are NOT.
"""
from __future__ import annotations

from typing import Any, Dict, List

from _common.sec_statics.classification import (
    DEFAULT_SECTOR_ID,
    DEFAULT_INDUSTRY_ID,
    classify_index,
)

from builds.classification.sector_industry.index.stock.db import (
    fetch_stock_index_mapping,
    fetch_stock_meta,
)
from builds.classification.sector_industry.index.stock.dummy_ext import (
    dummy_code,
    ensure_dummy_index,
)
from builds.classification.sector_industry.stock_overrides import match_stock_override


async def classify_stocks(
    conn,
    indices: Dict[str, Any],
    catalog: Dict[str, Any],
    verbose: bool = True,
) -> List[Dict[str, Any]]:
    """Map stocks to indices (all qualifying, weight > 2%, excl. strategy-only).

    Returns the ``stocks`` list (one row per qualifying index per stock).
    Always recomputed from DB sec_composition each run.

    ``catalog`` is the sector → industry catalog (from build_catalog()) used
    to look up labels for stock-overrides dummy indices.
    """
    stocks: List[Dict[str, Any]] = []
    if conn is not None:
        stock_index_map = await fetch_stock_index_mapping(conn)
        stock_meta = await fetch_stock_meta(conn)
    else:
        stock_index_map = {}
        stock_meta = {}

    for stock_code, meta in stock_meta.items():
        # Exchange comes straight from the DB column (stats.stock_identity
        # .exchange) — never derived from the code suffix.
        exchange = meta["exchange"]
        mappings = stock_index_map.get(stock_code, [])
        # Filter out strategy-primary indices (is_industry_not_strategy=FALSE).
        # These are pure strategy/theme indices (BROAD, DIV, REGION, ...) with
        # no industry classification — they convey no industry info for stocks.
        # A stock whose top-weight parent index is strategy-primary is NOT
        # assigned to it; instead the next qualifying index where
        # is_industry_not_strategy=TRUE is used (and becomes primary if it
        # carries the max weight among the remaining qualifying indices).
        qualifying = [
            (idx_code, idx_weight)
            for idx_code, idx_weight in mappings
            if idx_code not in indices
            or indices[idx_code].get("is_industry_not_strategy", True)
        ]

        if qualifying:
            # parent_index_is_primary: exactly ONE row per code — the one with
            # MAX(parent_index_weight).  fetch_stock_index_mapping returns
            # rows sorted by weight DESC then code, so the first row tied at
            # the max wins (deterministic tie-break).  Only that single row is
            # marked primary; other rows tied at the same max weight are NOT.
            max_weight = max(w for _, w in qualifying)
            primary_assigned = False
            for idx_code, idx_weight in qualifying:
                idx = indices.get(idx_code)
                if idx is not None:
                    # Inherit the parent's PRIMARY (sector_id, industry_id).
                    sector_id = idx["sector_id"]
                    industry_id = idx["industry_id"]
                    is_ind = idx.get("is_industry_not_strategy", True)
                else:
                    sector_id = DEFAULT_SECTOR_ID
                    industry_id = DEFAULT_INDUSTRY_ID
                    is_ind = True
                # Stock exchange is the stock_identity.exchange DB column
                # (A-share listing venue) — HK/overseas-themed indices keep
                # the stock's own A-share exchange (e.g. 000063.SZ 中兴通讯
                # held by SHS科技100 is a SZ-listed A-share, NOT an HK stock).
                stock_exchange = exchange
                is_primary = (not primary_assigned) and (idx_weight == max_weight)
                if is_primary:
                    primary_assigned = True
                stocks.append({
                    "code": stock_code,
                    "name": meta["name"],
                    "exchange": stock_exchange,
                    "parent_index_code": idx_code,
                    "parent_index_weight": round(idx_weight, 4),
                    "parent_index_is_primary": is_primary,
                    "sector_id": sector_id,
                    "industry_id": industry_id,
                    "is_industry_not_strategy": is_ind,
                    "n_days": meta["n_days"],
                    "first_date": meta["first_date"],
                    "last_date": meta["last_date"],
                    # Stock owners (listed company names) are not curated in
                    # sec_owners.json yet — leave NULL until a company-name
                    # source is wired in.
                    "owner_id": None,
                })
        else:
            # No qualifying industry index.  Try THREE fallbacks in order:
            #
            # 1. INDUSTRY name-based classification (classify_index) — catches
            #    stock names containing industry keywords (e.g. "深圳能源" →
            #    能源, "中环环保" → 环保).  Strategy rules are NOT tried.
            # 2. stock_overrides name-pattern map — catches stock-specific
            #    patterns not in INDUSTRY_RULES (e.g. "中原高速" → EXPRESSWAY,
            #    "美好置业" → RE_REAL_ESTATE).  Also assigns a DUMMY parent
            #    index so the stock appears in the UI hierarchy.
            # 3. If neither matches, the stock stays at (OTHER, OTHER).
            stock_name = meta["name"]
            sector_id = DEFAULT_SECTOR_ID
            industry_id = DEFAULT_INDUSTRY_ID
            is_ind = True
            parent_code = ""

            # Fallback 1: INDUSTRY_RULES keyword match.
            name_sector, _, name_industry, _ = classify_index(stock_name)
            if name_sector != DEFAULT_SECTOR_ID:
                sector_id = name_sector
                industry_id = name_industry

            # Fallback 2: stock_overrides name-pattern map.
            # Overrides the sector/industry if it matches AND assigns a
            # DUMMY parent index.  Also applies when fallback 1 matched but
            # the stock still has no parent_index_code — the dummy gives it
            # a place in the hierarchy.
            if sector_id == DEFAULT_SECTOR_ID:
                # Fallback 1 didn't match — try stock_overrides.
                override = match_stock_override(stock_name)
                if override is not None:
                    sector_id, industry_id, parent_code = override
                    ensure_dummy_index(indices, catalog, sector_id, industry_id)
            else:
                # Fallback 1 matched — assign a DUMMY parent for the
                # matched industry so the stock has a parent_index_code.
                parent_code = dummy_code(industry_id)
                ensure_dummy_index(indices, catalog, sector_id, industry_id)

            stocks.append({
                "code": stock_code,
                "name": stock_name,
                "exchange": exchange,
                "parent_index_code": parent_code,
                "parent_index_weight": None,
                "parent_index_is_primary": False,
                "sector_id": sector_id,
                "industry_id": industry_id,
                "is_industry_not_strategy": is_ind,
                "n_days": meta["n_days"],
                "first_date": meta["first_date"],
                "last_date": meta["last_date"],
                "owner_id": None,
            })

    if verbose:
        n_stock_codes = len(set(s["code"] for s in stocks))
        n_mapped = sum(1 for s in stocks if s["parent_index_code"])
        n_dummy = sum(1 for s in stocks
                      if s["parent_index_code"].startswith("DUMMY_"))
        n_other = sum(1 for s in stocks
                      if not s["parent_index_code"] and s["sector_id"] == DEFAULT_SECTOR_ID)
        n_name_classified = sum(
            1 for s in stocks
            if s["parent_index_code"] and s["sector_id"] != DEFAULT_SECTOR_ID)
        n_primary = sum(1 for s in stocks if s["parent_index_is_primary"])
        print(f"    [STOCKS] {n_stock_codes} stocks → {len(stocks)} rows "
              f"({n_mapped} with parent, {n_dummy} via dummy, "
              f"{n_name_classified} name-classified, "
              f"{n_other} → OTHER, {n_primary} primary)", flush=True)

    return stocks
