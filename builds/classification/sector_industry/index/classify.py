"""Index classification leaf.

Indices are the first level under sector_industry: each index is assigned a
PRIMARY (sector_id, industry_id) pair plus the full set of industry/strategy
tags.  Classification is loaded from the JSON cache (the authoritative,
hand-editable source) when available; new indices not yet in the JSON are
classified by keyword rules.  ``--reclassify`` forces every index found in
CSV/DB to be reclassified from keyword rules (manually-added JSON-only
indices are always preserved as-is).
"""
from __future__ import annotations

from typing import Any, Dict, List

from _common.sec_statics.classification import (
    DEFAULT_SECTOR_ID,
    DEFAULT_INDUSTRY_ID,
    STRATEGY_SECTOR_IDS,
    classify_index_all_tags,
    classify_index_both,
)

from builds.classification.sector_industry.exchange import _exchange_from_index_code
from builds.classification.sector_industry.index.db import fetch_index_meta


async def classify_indices(
    conn,
    etf_rows: List[Dict[str, Any]],
    prev_indices: Dict[str, Any],
    reclassify_indices: bool = False,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Classify all indices discovered in the CSV and index_identity.

    Returns the ``indices`` dict keyed by index code.  Each value carries the
    PRIMARY classification in sector_id/industry_id (industry when
    is_industry_not_strategy=TRUE, strategy otherwise) plus ``tags`` (ALL
    classifications for sec_index_tags) and coverage metadata.

    Indices present in the JSON cache but not in the CSV/DB are preserved
    (the JSON is authoritative and hand-editable).
    """
    # Collect index names from the CSV and index_identity.  The JSON itself
    # (sec_classification.json) is the authoritative, hand-editable cache —
    # new indices discovered here are classified by keyword rules and merged
    # in below.
    index_names: Dict[str, str] = {}  # code → name
    for r in etf_rows:
        if r["index_code"] and r["index_code"] not in index_names:
            index_names[r["index_code"]] = r["index_name"]
    if conn is not None:
        db_index_meta = await fetch_index_meta(conn)
        for code, meta in db_index_meta.items():
            if code not in index_names:
                index_names[code] = meta["name"]
    else:
        db_index_meta = {}

    indices: Dict[str, Any] = {}
    n_from_json = 0
    n_new_classified = 0
    n_reclassified = 0
    for code, name in index_names.items():
        meta = db_index_meta.get(code, {})
        prev = prev_indices.get(code)
        use_json = prev is not None and not reclassify_indices
        if use_json:
            # Use the hand-tuned classification from the JSON cache.
            # sector_id/industry_id hold the PRIMARY classification
            # (industry when is_ind=TRUE, strategy when is_ind=FALSE).
            sector_id = prev.get("sector_id", DEFAULT_SECTOR_ID)
            industry_id = prev.get("industry_id", DEFAULT_INDUSTRY_ID)
            is_ind = prev.get("is_industry_not_strategy")
            if is_ind is None:
                is_ind = sector_id != DEFAULT_SECTOR_ID
            # Old-JSON migration: prior versions stored the INDUSTRY
            # classification in sector_id/industry_id and the STRATEGY
            # classification in separate strategy_id/theme_id fields.  When
            # strategy is primary (is_ind=FALSE), fold strategy_id/theme_id
            # into sector_id/industry_id so the state carries the PRIMARY.
            if not is_ind and "strategy_id" in prev:
                sector_id = prev.get("strategy_id", sector_id)
                industry_id = prev.get("theme_id", industry_id)
            # Preserve hand-edited tags; fall back to computing from name.
            # classify_index_all_tags returns BOTH industry and strategy tags
            # so sec_index_tags stores all classifications per index.
            # If cached tags lack any strategy sector_id (e.g. migrated from
            # old JSON that stored strategy_tags separately), recompute from
            # the name so both dimensions are present.
            tags = prev.get("tags")
            if tags is None or not any(
                t.get("sector_id") in STRATEGY_SECTOR_IDS for t in tags
            ):
                all_tags = classify_index_all_tags(name)
                tags = [{"sector_id": t[0], "industry_id": t[2]} for t in all_tags]
            n_from_json += 1
        else:
            # New index OR --reclassify: classify by keyword rules (both
            # dims) and pick the PRIMARY (sector_id, industry_id) pair —
            # industry when it matched, strategy otherwise.
            ind_tup, strat_tup, is_ind = classify_index_both(name)
            if is_ind:
                sector_id, _, industry_id, _ = ind_tup
            else:
                sector_id, _, industry_id, _ = strat_tup
            all_tags = classify_index_all_tags(name)
            tags = [{"sector_id": t[0], "industry_id": t[2]} for t in all_tags]
            if prev is not None:
                n_reclassified += 1
            else:
                n_new_classified += 1
        indices[code] = {
            "name": name,
            "exchange": _exchange_from_index_code(code, name, sector_id, tags),
            "sector_id": sector_id,
            "industry_id": industry_id,
            "tags": tags,
            "is_industry_not_strategy": is_ind,
            "n_days": meta.get("n_days", 0),
            "first_date": meta.get("first_date"),
            "last_date": meta.get("last_date"),
        }

    # Preserve indices that exist in the JSON cache but not in the CSV/DB.
    # The JSON is the authoritative, hand-editable cache — indices added
    # manually (e.g. 399812 养老产业, 931746 储能产业, 000970 中证ESG40)
    # must survive rebuilds even when no ETF tracks them.
    for code, prev in prev_indices.items():
        if code not in indices:
            prev = dict(prev)
            is_ind = prev.get("is_industry_not_strategy")
            if is_ind is None:
                is_ind = prev.get("sector_id", DEFAULT_SECTOR_ID) != DEFAULT_SECTOR_ID
            prev["is_industry_not_strategy"] = is_ind
            # Old-JSON migration: fold strategy into sector_id/industry_id
            # when strategy is primary (see note above).
            if not is_ind and "strategy_id" in prev:
                prev["sector_id"] = prev.get("strategy_id", prev.get("sector_id", DEFAULT_SECTOR_ID))
                prev["industry_id"] = prev.get("theme_id", prev.get("industry_id", DEFAULT_INDUSTRY_ID))
            # Drop legacy strategy fields so save_json writes the new schema.
            for _legacy in ("strategy_id", "strategy_label", "theme_id",
                            "theme_label", "strategy_tags"):
                prev.pop(_legacy, None)
            # Recompute tags if they lack strategy sector_ids (migrated JSON
            # stored strategy_tags separately — see note above).
            tags = prev.get("tags")
            if tags is None or not any(
                t.get("sector_id") in STRATEGY_SECTOR_IDS for t in tags
            ):
                all_tags = classify_index_all_tags(prev.get("name", ""))
                prev["tags"] = [{"sector_id": t[0], "industry_id": t[2]} for t in all_tags]
            # Re-derive exchange from code + name + classification so manually
            # added JSON-only indices pick up the same SS/SZ/BJ/HK/OVERSEAS
            # logic as CSV/DB-discovered indices.
            prev["exchange"] = _exchange_from_index_code(
                code, prev.get("name", ""),
                prev.get("sector_id"), prev.get("tags"))
            indices[code] = prev
            n_from_json += 1

    if verbose:
        # sector_id holds the PRIMARY classification, so a non-OTHER
        # sector_id means the index matched EITHER an industry OR a strategy
        # rule.  is_industry_not_strategy tells which dimension matched.
        n_industry_matched = sum(
            1 for v in indices.values()
            if v.get("is_industry_not_strategy") and v["sector_id"] != DEFAULT_SECTOR_ID)
        n_strategy_matched = sum(
            1 for v in indices.values()
            if not v.get("is_industry_not_strategy") and v["sector_id"] != DEFAULT_SECTOR_ID)
        n_multi = sum(1 for v in indices.values() if len(v.get("tags", [])) > 1)
        n_ind_primary = sum(1 for v in indices.values() if v.get("is_industry_not_strategy"))
        reclass_note = f", {n_reclassified} reclassified" if n_reclassified else ""
        print(f"    [INDICES] {len(indices)} indices "
              f"({n_from_json} from JSON, {n_new_classified} newly classified{reclass_note}, "
              f"{n_industry_matched} industry-matched, {n_strategy_matched} strategy-matched, "
              f"{n_ind_primary} industry-primary, {len(indices) - n_ind_primary} strategy-primary, "
              f"{n_multi} multi-tag)", flush=True)

    return indices
