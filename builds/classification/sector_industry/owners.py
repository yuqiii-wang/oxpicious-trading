"""Owners — load sec_owners.json + match ETF manager / name to owner_id.

sec_owners.json is a curated, hand-editable registry of security owners
(ETF fund managers / issuers, stock companies).  Each entry has:
  owner_id    — stable slug (PK in stats.sec_owners)
  name        — short display name (e.g. 南方, 华夏, 国泰君安)
  type        — fund_manager / broker / asset_manager / insurance / bank / ...
  aliases     — short-name prefixes matched against ETF names (longest wins)
  full_names  — legal entity names matched against the CSV `管理人` column

Matching priority for ETFs:
  1. CSV `管理人` exact match against full_names  (most reliable)
  2. ETF name prefix match against aliases        (longest alias wins)
  3. NULL                                         (no curated owner)

The matched alias is also returned so the caller can strip it from the ETF
name (plus the standard legal suffix) to test whether the cleaned name
exactly equals the parent index name — that equality is the criterion for
parent_index_is_primary = TRUE for ETFs.

``upsert_owners`` rebuilds stats.sec_owners from the curated JSON each run
so the table stays in sync with hand edits.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from _common.build_commons import bulk_upsert_async, truncate_table_async

from builds.classification.sector_industry.paths import OWNERS_JSON_PATH


def load_owners() -> List[Dict[str, Any]]:
    """Load sec_owners.json. Returns a list of owner dicts (empty on failure)."""
    if not os.path.isfile(OWNERS_JSON_PATH):
        print(f"    [WARN] {OWNERS_JSON_PATH} not found — owner_id will be NULL",
              flush=True)
        return []
    try:
        with open(OWNERS_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        owners = data.get("owners", [])
        print(f"    [OWNERS] Loaded {len(owners)} owners from "
              f"{os.path.basename(OWNERS_JSON_PATH)}", flush=True)
        return owners
    except Exception as e:
        print(f"    [WARN] Failed to load {OWNERS_JSON_PATH}: {e}",
              flush=True)
        return []


def build_owner_matchers(
    owners: List[Dict[str, Any]],
) -> Tuple[Dict[str, str], List[Tuple[str, str]]]:
    """Build lookup structures from the owners list.

    Returns:
        full_name_to_id   — {legal_entity_name: owner_id} for exact 管理人 matching
        alias_pairs       — [(alias, owner_id), ...] sorted by alias length DESC
                            so the longest prefix match wins (国泰君安 before 国泰)
    """
    full_name_to_id: Dict[str, str] = {}
    for o in owners:
        for fn in o.get("full_names", []):
            full_name_to_id[fn] = o["owner_id"]

    alias_pairs: List[Tuple[str, str]] = []
    for o in owners:
        # Always include the short `name` as an alias too, so an owner whose
        # JSON entry omits `aliases` still matches by its display name.
        seen = set()
        for a in [o.get("name", "")] + o.get("aliases", []):
            a = (a or "").strip()
            if a and a not in seen:
                seen.add(a)
                alias_pairs.append((a, o["owner_id"]))
    # Longest alias first — ensures "国泰君安" wins over "国泰" when the ETF
    # name starts with "国泰君安...".
    alias_pairs.sort(key=lambda x: len(x[0]), reverse=True)
    return full_name_to_id, alias_pairs


def match_etf_owner(
    etf_name: str,
    manager: str,
    full_name_to_id: Dict[str, str],
    alias_pairs: List[Tuple[str, str]],
) -> Tuple[Optional[str], Optional[str]]:
    """Find the owner_id for an ETF and the alias that matched its name prefix.

    Returns (owner_id, matched_alias).  matched_alias is the alias string that
    was stripped from the ETF name prefix (None if no alias matched, e.g. when
    only the CSV `管理人` full_name matched).  Both are None when no owner is
    found.
    """
    etf_name = str(etf_name or "")
    manager = str(manager or "")

    # 1. Exact full-name match on the CSV `管理人` column (most reliable).
    if manager and manager in full_name_to_id:
        owner_id = full_name_to_id[manager]
        # Still try to find the alias that prefixes the ETF name so the caller
        # can strip it for the primary-name test.
        for alias, oid in alias_pairs:
            if oid == owner_id and etf_name.startswith(alias):
                return owner_id, alias
        return owner_id, None

    # 2. Longest-alias prefix match on the ETF name.
    for alias, oid in alias_pairs:
        if etf_name.startswith(alias):
            return oid, alias

    return None, None


# Standard Chinese ETF legal-name suffix.  The full legal suffix is
# '交易型开放式指数证券投资基金' (sometimes followed by '(QDII)' or '(LOF)').
# Stripping everything from '交易型' onward removes '证券' (from '证券投资
# 基金') which would otherwise falsely match FIN/BROKERS keyword rules.
_ETF_LEGAL_SUFFIX_MARKER = "交易型"

# LOF (Listed Open-End Fund) suffix variants.  LOF-type funds use 'LOF' or
# '上市开放式基金' as their legal suffix instead of '交易型开放式指数'.
# These must be stripped so the remaining text (the index/theme name) can be
# matched against classification keywords.  Example: '申万环保LOF' → '环保'.
_LOF_SUFFIX_MARKERS = ("LOF", "上市开放式基金", "lof", "Lof")


def clean_etf_name_for_index_match(
    etf_name: str,
    matched_alias: Optional[str],
) -> str:
    """Strip the issuer/manager prefix and legal suffix from an ETF/LOF name.

    Used to test whether the remaining text exactly equals the parent index
    name (the criterion for parent_index_is_primary = TRUE for ETFs), and to
    produce a clean name for keyword-based classification fallback.

    Handles two legal-suffix families:
      * ETF: '交易型开放式指数证券投资基金' — strip from '交易型' onward.
      * LOF: 'LOF' / '上市开放式基金' — strip the LOF marker and everything
        after it, leaving the theme/industry keywords (e.g. '申万环保LOF' →
        after owner strip '申万' → '环保LOF' → after LOF strip → '环保').

    Examples:
        '南方中证全指食品交易型开放式指数证券投资基金' → '中证全指食品'
        '华夏上证50交易型开放式指数证券投资基金'       → '上证50'
        '易方达中证港股通内地金融交易型...'             → '中证港股通内地金融'
        '申万环保LOF'                                  → '环保'
    """
    s = str(etf_name or "")
    # Strip the matched issuer/manager prefix (if any).
    if matched_alias and s.startswith(matched_alias):
        s = s[len(matched_alias):]
    # Strip the ETF legal suffix (everything from '交易型' onward).
    idx = s.find(_ETF_LEGAL_SUFFIX_MARKER)
    if idx > 0:
        s = s[:idx]
    # Strip LOF suffix: remove 'LOF' / '上市开放式基金' and everything after.
    for marker in _LOF_SUFFIX_MARKERS:
        lof_idx = s.find(marker)
        if lof_idx > 0:
            s = s[:lof_idx]
            break
    # Strip trailing qualifiers that sometimes follow the index name.
    for tail in ("(QDII)", "(LOF)", "（QDII）", "（LOF）"):
        if s.endswith(tail):
            s = s[: -len(tail)]
    return s.strip()


async def upsert_owners(
    conn,
    owners: List[Dict[str, Any]],
    verbose: bool = True,
) -> None:
    """Rebuild stats.sec_owners from the curated sec_owners.json each run.

    Must run BEFORE sec_classification so the logical owner_id reference is
    always valid.  Truncate + rebuild keeps the table in sync with the curated
    JSON (stale entries removed when JSON is edited).
    """
    owner_rows: List[Dict[str, Any]] = []
    for o in owners:
        owner_rows.append({
            "owner_id": o["owner_id"],
            "name": o.get("name", ""),
            "type": o.get("type"),
            "aliases": list(o.get("aliases", [])),
            "full_names": list(o.get("full_names", [])),
        })
    if owner_rows:
        await truncate_table_async(conn, "stats.sec_owners")
        inserted = await bulk_upsert_async(
            conn, "stats.sec_owners", owner_rows, ["owner_id"])
        if verbose:
            print(f"    [DB] Inserted {inserted:,} owner rows into "
                  f"stats.sec_owners", flush=True)
