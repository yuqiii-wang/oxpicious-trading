"""ETF classification leaf — ETFs classified to their tracking index.

ETFs sit one level below index: each ETF inherits the PRIMARY
(sector_id, industry_id) of its tracking index (one-to-one ETF → index).
When the parent index is unclassified (OTHER) or missing, a name-based
fallback (classify_etf_by_name) applies the same keyword rules to the ETF
name; 'IB names' (foreign-branded ETFs lacking the standard Chinese
suffix) are classified by their underlying index_name instead.

Two special rules live here:
  * OVERWRITE RULE — if the ETF name (with fund-owner prefix + legal suffix
    stripped) EXACTLY matches an index name, parent_index_code + the
    classification are overwritten to that index (corrects wrong CSV
    mappings when the ETF name embeds the correct index verbatim).
  * parent_index_is_primary — TRUE iff that cleaned ETF name exactly
    equals the parent index name.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from _common.sec_statics.classification import (
    DEFAULT_SECTOR_ID,
    DEFAULT_INDUSTRY_ID,
    classify_etf_by_name,
    classify_etf_strategy_by_name,
)

from builds.classification.sector_industry.exchange import _is_hk_name, _is_overseas_name
from builds.classification.sector_industry.owners import (
    clean_etf_name_for_index_match,
    match_etf_owner,
)


def classify_etfs(
    etf_rows: List[Dict[str, Any]],
    indices: Dict[str, Any],
    full_name_to_id: Dict[str, str],
    alias_pairs: List[Tuple[str, str]],
    verbose: bool = True,
) -> Dict[str, Any]:
    """Map ETFs to their tracking index and inherit its classification.

    Returns the ``etfs`` dict keyed by ETF code (``{bare}.{exchange}``).
    Always recomputed from the CSV + index inheritance each run.
    """
    # When the parent index is unclassified (OTHER) or missing, fall back to
    # name-based classification via classify_etf_by_name().  ETF names
    # typically embed the index name, so keyword rules can classify them
    # directly.  'IB names' (foreign-branded ETFs) are classified by their
    # underlying index_name instead — see classify_etf_by_name().
    #
    # OVERWRITE RULE: if the ETF name (with fund-owner prefix + legal suffix
    # stripped) EXACTLY matches an index name, the ETF inherits that index's
    # classification AND its parent_index_code is overwritten to the matched
    # index.  This corrects cases where the CSV's ETF→index mapping points
    # to a wrong/placeholder index but the ETF name itself embeds the correct
    # index verbatim.  Example: "华夏沪深300ETF" → cleaned = "沪深300" →
    # matches index 000300 (沪深300), overwriting whatever index_code the CSV
    # had assigned.
    index_name_to_code: Dict[str, str] = {}
    for icode, idata in indices.items():
        iname = idata.get("name", "")
        if iname:
            index_name_to_code[iname] = icode

    etfs: Dict[str, Any] = {}
    n_name_classified = 0
    n_owner_matched = 0
    n_primary = 0
    n_overwritten = 0
    for r in etf_rows:
        bare_code = r["etf_code"]
        exchange = r["exchange"]
        if exchange is None:
            continue  # skip ETFs with unknown exchange
        etf_code = f"{bare_code}.{exchange}"
        etf_name = r["etf_name"]
        index_code = r["index_code"]
        idx = indices.get(index_code)
        if idx is not None:
            # Inherit the parent's PRIMARY (sector_id, industry_id) directly.
            sector_id = idx["sector_id"]
            industry_id = idx["industry_id"]
            is_ind = idx.get("is_industry_not_strategy", sector_id != DEFAULT_SECTOR_ID)
        else:
            sector_id = DEFAULT_SECTOR_ID
            industry_id = DEFAULT_INDUSTRY_ID
            is_ind = True
        # Fallback: if the parent's PRIMARY is unclassified (OTHER), try
        # classifying the ETF by its own name (or index_name for IB names) —
        # industry rules first, then strategy rules.  Whichever matches
        # becomes the primary (industry takes precedence over strategy).
        if sector_id == DEFAULT_SECTOR_ID:
            idx_name = idx["name"] if idx is not None else r.get("index_name", "")
            name_sector, _, name_industry, _ = classify_etf_by_name(
                etf_name, idx_name)
            if name_sector != DEFAULT_SECTOR_ID:
                sector_id = name_sector
                industry_id = name_industry
                is_ind = True
                n_name_classified += 1
            else:
                strat_sector, _, strat_industry, _ = classify_etf_strategy_by_name(
                    etf_name, idx_name)
                if strat_sector != DEFAULT_SECTOR_ID:
                    sector_id = strat_sector
                    industry_id = strat_industry
                    is_ind = False
                    n_name_classified += 1

        # Owner: match via CSV `管理人` full-name (preferred) or ETF-name alias.
        owner_id, matched_alias = match_etf_owner(
            etf_name, r.get("manager", ""), full_name_to_id, alias_pairs)
        if owner_id is not None:
            n_owner_matched += 1

        # parent_index_is_primary for ETFs: TRUE iff the ETF name (with the
        # issuer/manager prefix and legal suffix stripped) exactly matches the
        # parent index name.  Signals a "clean" tracker whose name embeds the
        # index verbatim (vs. IB names or names with extra qualifiers).
        parent_index_name = idx["name"] if idx is not None else r.get("index_name", "")
        cleaned = clean_etf_name_for_index_match(etf_name, matched_alias)
        is_primary = bool(parent_index_name) and cleaned == parent_index_name

        # OVERWRITE RULE: if the cleaned ETF name exactly matches an index
        # name, overwrite the parent_index_code + classification to that
        # index.  This corrects wrong CSV mappings when the ETF name embeds
        # the correct index verbatim.  Takes precedence over the original
        # CSV-derived index_code and the name-rule fallback above.
        matched_code = index_name_to_code.get(cleaned) if cleaned else None
        if matched_code and matched_code != index_code:
            ow_idx = indices[matched_code]
            index_code = matched_code
            sector_id = ow_idx["sector_id"]
            industry_id = ow_idx["industry_id"]
            is_ind = ow_idx.get("is_industry_not_strategy", sector_id != DEFAULT_SECTOR_ID)
            is_primary = True  # cleaned == matched index name by definition
            n_overwritten += 1
            # Refresh idx reference so the exchange override below uses the
            # overwritten parent's name/exchange.
            idx = ow_idx

        # Override exchange to 'HK' or 'OVERSEAS' when the ETF's underlying
        # tracks HK or non-Greater-China markets.  Detected via ETF name or
        # parent index name (HK keywords: 港股通/恒生/沪港深/SHS; overseas
        # keywords: 标普/纳斯达克/日经/德国/...) OR sector_id OVERSEAS.
        # Applies even when the ETF itself is listed on SH/SZ (e.g. 513050.SS
        # tracking 港股通互联, 159612.SZ 标普500ETF, 159502.SZ 标普生物科技).
        # Runs AFTER the OVERWRITE RULE so the final parent is used.
        idx_name = idx["name"] if idx is not None else r.get("index_name", "")
        if exchange != "HK" and (_is_hk_name(etf_name) or _is_hk_name(idx_name)):
            exchange = "HK"
        if exchange not in ("HK", "OVERSEAS") and (
            sector_id == "OVERSEAS"
            or _is_overseas_name(etf_name)
            or _is_overseas_name(idx_name)
        ):
            exchange = "OVERSEAS"

        if is_primary:
            n_primary += 1

        etfs[etf_code] = {
            "name": etf_name,
            "exchange": exchange,
            "parent_index_code": index_code,
            "parent_index_weight": None,
            "parent_index_is_primary": is_primary,
            "sector_id": sector_id,
            "industry_id": industry_id,
            "is_industry_not_strategy": is_ind,
            "aum_yi": r.get("aum_yi"),
            "owner_id": owner_id,
        }

    if verbose:
        n_with_index = sum(1 for v in etfs.values()
                           if v["parent_index_code"] and v["sector_id"] != DEFAULT_SECTOR_ID)
        n_other = sum(1 for v in etfs.values()
                      if v["sector_id"] == DEFAULT_SECTOR_ID)
        ow_note = f", {n_overwritten} overwritten" if n_overwritten else ""
        print(f"    [ETF] {len(etfs)} ETFs mapped "
              f"({n_with_index} with classified parent index, "
              f"{n_name_classified} by name rules{ow_note}, {n_other} → OTHER, "
              f"{n_owner_matched} owner-matched, {n_primary} primary)",
              flush=True)

    return etfs
