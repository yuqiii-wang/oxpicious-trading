"""Stock classification leaf — stocks classified to their qualifying indices.

Stocks sit one level below index (like ETFs) but are MANY-TO-MANY: each
qualifying index (weight > STOCK_WEIGHT_THRESHOLD, excluding strategy-primary
indices) produces ONE ROW, so a stock may have multiple rows in
sec_classification.  Stocks without any qualifying index → single row with
parent_index_code = '' and (OTHER, OTHER).

Strategy-primary indices (is_industry_not_strategy=FALSE, e.g. 沪深300 →
BROAD, 中证红利 → DIV) are excluded because they convey no industry
information.  Indices not in the ``indices`` dict (unclassified) are
included — the caller can classify them via JSON later.

parent_index_is_primary: exactly ONE row per code — the one with
MAX(parent_index_weight); other rows tied at the same max weight are NOT.
"""
from __future__ import annotations

from typing import Any, Dict, List

from _common.sec_statics.classification import (
    DEFAULT_SECTOR_ID,
    DEFAULT_INDUSTRY_ID,
    classify_index,
    classify_index_strategy,
)

from builds.classification.sector_industry.exchange import _exchange_from_code
from builds.classification.sector_industry.index.stock.db import (
    fetch_stock_index_mapping,
    fetch_stock_meta,
)


async def classify_stocks(
    conn,
    indices: Dict[str, Any],
    verbose: bool = True,
) -> List[Dict[str, Any]]:
    """Map stocks to indices (all qualifying, weight > 2%, excl. strategy-only).

    Returns the ``stocks`` list (one row per qualifying index per stock).
    Always recomputed from DB sec_composition each run.
    """
    stocks: List[Dict[str, Any]] = []
    if conn is not None:
        stock_index_map = await fetch_stock_index_mapping(conn)
        stock_meta = await fetch_stock_meta(conn)
    else:
        stock_index_map = {}
        stock_meta = {}

    for stock_code, meta in stock_meta.items():
        exchange = _exchange_from_code(stock_code)
        mappings = stock_index_map.get(stock_code, [])
        # Filter out strategy-primary indices (is_industry_not_strategy=FALSE).
        # These are pure strategy/theme indices (BROAD, DIV, REGION, ...) with
        # no industry classification — they convey no industry info for stocks.
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
                # Stock exchange comes from the stock code's .SZ/.SS/.BJ
                # suffix ONLY — A-share stocks held by HK/overseas-themed
                # indices keep their own exchange (they are still A-share
                # listed, e.g. 000063.SZ 中兴通讯 held by SHS科技100 is a
                # SZ-listed A-share, NOT an HK stock).  Actual HK-listed
                # stocks carry a .HK suffix and are handled by
                # _exchange_from_code directly.
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
            # No qualifying index: try name-based classification as a fallback.
            # Stock names are company names (not index names), so matching is
            # less reliable than for ETFs — but many contain industry keywords
            # (e.g. "深圳能源" → 能源, "中环环保" → 环保).  Industry rules
            # take precedence over strategy rules.
            stock_name = meta["name"]
            sector_id = DEFAULT_SECTOR_ID
            industry_id = DEFAULT_INDUSTRY_ID
            is_ind = True
            name_sector, _, name_industry, _ = classify_index(stock_name)
            if name_sector != DEFAULT_SECTOR_ID:
                sector_id = name_sector
                industry_id = name_industry
                is_ind = True
            else:
                strat_sector, _, strat_industry, _ = classify_index_strategy(
                    stock_name)
                if strat_sector != DEFAULT_SECTOR_ID:
                    sector_id = strat_sector
                    industry_id = strat_industry
                    is_ind = False
            stocks.append({
                "code": stock_code,
                "name": stock_name,
                "exchange": exchange,
                "parent_index_code": "",
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
        n_other = sum(1 for s in stocks
                      if not s["parent_index_code"] and s["sector_id"] == DEFAULT_SECTOR_ID)
        n_name_classified = sum(
            1 for s in stocks
            if not s["parent_index_code"] and s["sector_id"] != DEFAULT_SECTOR_ID)
        n_primary = sum(1 for s in stocks if s["parent_index_is_primary"])
        print(f"    [STOCKS] {n_stock_codes} stocks → {len(stocks)} rows "
              f"({n_mapped} mapped to index, {n_name_classified} by name, "
              f"{n_other} → OTHER, {n_primary} primary)", flush=True)

    return stocks
