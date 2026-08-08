"""DB upsert for the index leaf.

Upserts indices (type='index') and the multi-tag sec_index_tags table.
Indices are the root of the hierarchy — parent_index_code = '' and
parent_index_is_primary = FALSE; they have no owner.
"""
from __future__ import annotations

from typing import Any, Dict, List

from _common.build_commons import bulk_upsert_async, truncate_table_async

from builds.classification.sector_industry.catalog import _lookup_labels, _parse_date


async def upsert_indices(
    conn,
    catalog: Dict[str, Any],
    indices: Dict[str, Any],
    verbose: bool = True,
) -> None:
    """Upsert indices (type='index') into stats.sec_classification.

    parent_index_code = '' (root of hierarchy); PK is (code, parent_index_code).
    Indices have no parent and no owner → parent_index_is_primary=FALSE,
    owner_id=NULL.  v["sector_id"]/v["industry_id"] already hold the PRIMARY
    classification (industry when is_ind=TRUE, strategy when is_ind=FALSE),
    so labels are looked up directly from the unified catalog (covers both).
    """
    index_rows: List[Dict[str, Any]] = []
    for code, v in indices.items():
        fd = v.get("first_date")
        ld = v.get("last_date")
        is_ind = v.get("is_industry_not_strategy", True)
        sector_label, industry_label, industry_slug = _lookup_labels(
            catalog, v["sector_id"], v["industry_id"])
        index_rows.append({
            "code": code,
            "name": v["name"],
            "type": "index",
            "exchange": v.get("exchange"),
            "sector_id": v["sector_id"],
            "sector_label": sector_label,
            "industry_id": v["industry_id"],
            "industry_label": industry_label,
            "industry_slug": industry_slug,
            "is_industry_not_strategy": is_ind,
            "n_days": v.get("n_days", 0),
            "first_date": _parse_date(fd),
            "last_date": _parse_date(ld),
            "parent_index_code": "",
            "parent_index_weight": None,
            "parent_index_is_primary": False,
            "owner_id": None,
            "is_dummy": v.get("is_dummy", False),
        })
    if index_rows:
        inserted = await bulk_upsert_async(
            conn, "stats.sec_classification", index_rows,
            ["code", "parent_index_code"])
        if verbose:
            print(f"    [DB] Upserted {inserted:,} index rows into "
                  f"stats.sec_classification", flush=True)


async def upsert_index_tags(
    conn,
    indices: Dict[str, Any],
    verbose: bool = True,
) -> None:
    """Upsert index tags (sec_index_tags).

    Stores ALL classifications per index (multi-tag). Truncate + rebuild
    each run so stale tags are removed when the JSON is hand-edited.
    is_broad_market: TRUE iff the index's PRIMARY classification is the
    BROAD strategy (is_industry_not_strategy=FALSE AND sector_id='BROAD').
    Since sector_id holds the PRIMARY, this is read directly —
    industry-primary indices whose secondary tag happens to be BROAD do
    NOT set this flag.
    """
    tag_rows: List[Dict[str, Any]] = []
    for code, v in indices.items():
        tags = v.get("tags")
        if not tags:
            tags = [{"sector_id": v["sector_id"], "industry_id": v["industry_id"]}]
        is_ind = v.get("is_industry_not_strategy", True)
        is_broad = (not is_ind) and (v["sector_id"] == "BROAD")
        for tag in tags:
            tag_rows.append({
                "code": code,
                "sector_id": tag["sector_id"],
                "industry_id": tag["industry_id"],
                "is_broad_market": is_broad,
                "is_industry_not_strategy": is_ind,
            })
    if tag_rows:
        await truncate_table_async(conn, "stats.sec_index_tags")
        inserted = await bulk_upsert_async(
            conn, "stats.sec_index_tags", tag_rows,
            ["code", "sector_id", "industry_id"])
        if verbose:
            print(f"    [DB] Inserted {inserted:,} index tag rows into "
                  f"stats.sec_index_tags", flush=True)
