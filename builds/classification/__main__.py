"""
build_classification.py — Build two-level (sector → industry) security
classification for ETF + Index + Stock, persist INDEX classification to
sec_classification.json, and upsert all to stats.sec_classification +
stats.sec_index_tags.

Classification model:
  1. INDICES — classification (sector, industry) is loaded directly from
     sec_classification.json (the authoritative, hand-editable cache).
     New indices not yet in the JSON are classified by keyword rules and
     added to the JSON on the next save.
     parent_index_code = '' (empty string, root of hierarchy).
  2. ETFs — always recomputed from CSV + index inheritance (one-to-one
     ETF → tracking index).  When the parent index is unclassified (OTHER)
     or missing, a name-based fallback (classify_etf_by_name) applies the
     same keyword rules to the ETF name.  'IB names' (foreign-branded ETFs
     lacking the standard Chinese suffix) are classified by their
     underlying index_name instead.
     parent_index_code = tracking index code.
  3. STOCKS — always recomputed from DB sec_composition.  ONE ROW PER
     qualifying index (weight > 2%, excluding BROAD-sector indices).
     A stock may therefore have multiple rows in sec_classification.
     parent_index_code = each qualifying index code, parent_index_weight.
     Stocks without any qualifying index → single row with
     parent_index_code = '' and (OTHER, OTHER).

The JSON contains ONLY the catalog + index classifications (no ETF/stock
data).  ETF and stock mappings are rebuilt every run and upserted to the
DB directly — they are never persisted to the JSON.

The sector → industry catalog allows OVERLAPPING sectors: the same
industry_id may appear under multiple sector_ids (e.g. POWER_EQUIP can be
both ENG and IND).  Each security is assigned exactly ONE (sector_id,
industry_id) pair.

Labels are DENORMALIZED: every sec_classification row carries its own
sector_label, industry_label, industry_slug (looked up from the catalog at
upsert time).  This eliminates the need for a separate catalog table —
the former stats.sec_sector_industry_map has been DROPPED.

Usage:
  python build_classification.py                # load JSON indices + recompute ETFs/stocks + save JSON + upsert DB
  python build_classification.py --no-db        # same but skip DB upsert
  python build_classification.py --force        # truncate sec_classification before upsert (removes stale rows)
  python build_classification.py --reclassify   # reclassify ALL indices from keyword rules (ignores JSON cache)
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from utils.build_commons import (
    setup_utf8_stdout, get_db_or_exit,
    print_build_header, print_wall_time,
    PROJECT_ROOT, TODAY_STR,
    bulk_upsert_async, truncate_table_async,
    add_force_arg,
)

setup_utf8_stdout()


# ============================================================================
# Paths
# ============================================================================
CSV_DIR = os.path.join(PROJECT_ROOT, "temps", "csindex_linked_etf")
JSON_PATH = os.path.join(PROJECT_ROOT, "utils", "sec_classification.json")
OWNERS_JSON_PATH = os.path.join(PROJECT_ROOT, "utils", "sec_owners.json")


# ============================================================================
# Index classification rules
#   (sector_id, sector_label, industry_id, industry_label, [keywords])
#   Ordered by priority: sector-specific rules first, broad-market last.
#   Scoring: (total_kw_len, n_hits, longest_kw) — highest wins.
# ============================================================================

from utils.classification import (
    INDEX_RULES,
    RULE_ORDER as _RULE_ORDER,
    DEFAULT_SECTOR_ID,
    DEFAULT_SECTOR_LABEL,
    DEFAULT_INDUSTRY_ID,
    DEFAULT_INDUSTRY_LABEL,
    classify_index,
    classify_index_tags,
    classify_etf_by_name,
)

# Overlapping catalog entries: same industry_id under multiple sector_ids.
# These are added to the catalog even if no security is assigned to them,
# documenting that the industry conceptually spans multiple sectors.
OVERLAPPING_CATALOG: List[Tuple[str, str, str, str]] = [
    # (sector_id, sector_label, industry_id, industry_label)
    ("ENG", "能源", "POWER_EQUIP", "电力设备"),
    ("IND", "工业", "POWER_EQUIP", "电力设备"),
    ("NEV", "新能源", "PV", "光伏"),
    ("IND", "工业", "PV", "光伏"),
    ("NEV", "新能源", "BATTERY", "储能/电池"),
    ("IND", "工业", "BATTERY", "储能/电池"),
]

# Stock → index weight threshold: only map a stock to an index if the stock's
# weight in that index exceeds this percentage.
STOCK_WEIGHT_THRESHOLD = 2.0


def build_catalog() -> Dict[str, Dict[str, Any]]:
    """Build the sector → industry catalog from INDEX_RULES + overlapping entries.

    Returns:
        { sector_id: { "label": ..., "industries": { industry_id: { "label": ..., "slug": ... } } } }
    """
    catalog: Dict[str, Dict[str, Any]] = {}

    def _add(sector_id: str, sector_label: str, industry_id: str, industry_label: str):
        if sector_id not in catalog:
            catalog[sector_id] = {"label": sector_label, "industries": {}}
        catalog[sector_id]["label"] = sector_label
        if industry_id not in catalog[sector_id]["industries"]:
            catalog[sector_id]["industries"][industry_id] = {
                "label": industry_label,
                "slug": industry_id.lower(),
            }
        else:
            catalog[sector_id]["industries"][industry_id]["label"] = industry_label

    # Add OTHER default
    _add(DEFAULT_SECTOR_ID, DEFAULT_SECTOR_LABEL, DEFAULT_INDUSTRY_ID, DEFAULT_INDUSTRY_LABEL)

    for sector_id, sector_label, industry_id, industry_label, _ in INDEX_RULES:
        _add(sector_id, sector_label, industry_id, industry_label)

    for sector_id, sector_label, industry_id, industry_label in OVERLAPPING_CATALOG:
        _add(sector_id, sector_label, industry_id, industry_label)

    return catalog


# ============================================================================
# CSV reading — ETF → index mapping
# ============================================================================

# Keywords in security/index/ETF names that indicate the underlying stocks are
# Hong Kong-listed.  Detection is name-based because HK-themed ETFs are often
# listed on SH/SZ (so 上市地 is 上海/深圳) while their holdings are HK stocks.
# "中华" alone is NOT included — "中华半导体芯片" tracks A-shares; HK variants
# like "中华港股通精选100" are already caught by "港股" below.
_HK_NAME_KEYWORDS: List[str] = [
    "港股通", "港股", "香港", "恒生", "恒指",
    "H股", "红筹", "沪港深", "深港沪", "SHS",
]


def _is_hk_name(name: str) -> bool:
    """Return True if the name indicates Hong Kong-listed underlying stocks.

    Checks for HK-related keywords (港股通, 港股, 香港, 恒生, 沪港深, SHS, etc.)
    in the security/index/ETF name.  This is used to set exchange='HK' for
    ETFs and indices whose main holdings are HK-listed, even when the ETF
    itself is listed on SH or SZ.
    """
    s = str(name)
    return any(kw in s for kw in _HK_NAME_KEYWORDS)


def _exchange_from_listed_at(listed_at: str) -> Optional[str]:
    """Map 上市地 (listing venue) to exchange code."""
    s = str(listed_at)
    if "上海" in s:
        return "SS"
    if "深圳" in s:
        return "SZ"
    if "北京" in s:
        return "BJ"
    if "香港" in s:
        return "HK"
    return None


def find_latest_csv() -> Optional[str]:
    """Find the latest ETF index map CSV in CSV_DIR.

    Prefers ``etf_index_map_all_YYYY-MM-DD.csv`` (unfiltered — ALL ETFs with
    AUM data) over the filtered ``etf_index_map_YYYY-MM-DD.csv``.  The
    ``all`` version is the authoritative source for ETF → index linkage and
    AUM (资产净值（亿元）) since it includes every ETF regardless of size.
    """
    if not os.path.isdir(CSV_DIR):
        return None
    # Prefer the unfiltered "all" version.
    all_candidates = sorted(
        f for f in os.listdir(CSV_DIR)
        if f.startswith("etf_index_map_all_") and f.endswith(".csv")
    )
    if all_candidates:
        return os.path.join(CSV_DIR, all_candidates[-1])
    # Fallback to filtered version.
    candidates = sorted(
        f for f in os.listdir(CSV_DIR)
        if f.startswith("etf_index_map_") and f.endswith(".csv") and "all" not in f
    )
    return os.path.join(CSV_DIR, candidates[-1]) if candidates else None


def load_etf_index_csv(csv_path: str) -> List[Dict[str, Any]]:
    """Load the ETF → index mapping CSV.

    Returns a list of dicts with keys:
        etf_code (bare 6-digit), etf_name, index_code, index_name, exchange,
        aum_yi (net asset value in 亿元, or None), manager (管理人 legal name)
    """
    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig")
    # Normalize column names (strip BOM, whitespace)
    df.columns = [c.strip().lstrip("\ufeff") for c in df.columns]

    results: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        etf_code = str(row.get("产品代码", "")).strip()
        etf_name = str(row.get("产品名称", "")).strip()
        index_code = str(row.get("标的指数代码", "")).strip()
        index_name = str(row.get("标的指数", "")).strip()
        listed_at = str(row.get("上市地", "")).strip()
        if not etf_code or not index_code:
            continue
        exchange = _exchange_from_listed_at(listed_at)
        # HK-listed ETFs have code format "XXXX HK Equity" — extract the
        # numeric prefix so it can be stored as a bare code with .HK suffix.
        if exchange == "HK" and " HK" in etf_code:
            etf_code = etf_code.split(" ")[0]
        # Parse AUM (资产净值 in 亿元) — may be empty for newly listed ETFs.
        aum_str = str(row.get("资产净值（亿元）", "")).strip()
        try:
            aum_yi = float(aum_str) if aum_str else None
        except (ValueError, TypeError):
            aum_yi = None
        # 管理人 (fund manager / issuer legal entity name) — used to match
        # the owner_id from sec_owners.json. May be empty for some rows.
        manager = str(row.get("管理人", "")).strip()
        results.append({
            "etf_code": etf_code,
            "etf_name": etf_name,
            "index_code": index_code,
            "index_name": index_name,
            "exchange": exchange,
            "aum_yi": aum_yi,
            "manager": manager,
        })
    return results


# ============================================================================
# Owners — load sec_owners.json + match ETF manager / name to owner_id
# ============================================================================
#
# sec_owners.json is a curated, hand-editable registry of security owners
# (ETF fund managers / issuers, stock companies).  Each entry has:
#   owner_id    — stable slug (PK in stats.sec_owners)
#   name        — short display name (e.g. 南方, 华夏, 国泰君安)
#   type        — fund_manager / broker / asset_manager / insurance / bank / ...
#   aliases     — short-name prefixes matched against ETF names (longest wins)
#   full_names  — legal entity names matched against the CSV `管理人` column
#
# Matching priority for ETFs:
#   1. CSV `管理人` exact match against full_names  (most reliable)
#   2. ETF name prefix match against aliases        (longest alias wins)
#   3. NULL                                         (no curated owner)
#
# The matched alias is also returned so the caller can strip it from the ETF
# name (plus the standard legal suffix) to test whether the cleaned name
# exactly equals the parent index name — that equality is the criterion for
# parent_index_is_primary = TRUE for ETFs.
# ============================================================================


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


def clean_etf_name_for_index_match(
    etf_name: str,
    matched_alias: Optional[str],
) -> str:
    """Strip the issuer/manager prefix and legal suffix from an ETF name.

    Used to test whether the remaining text exactly equals the parent index
    name (the criterion for parent_index_is_primary = TRUE for ETFs).

    Examples:
        '南方中证全指食品交易型开放式指数证券投资基金' → '中证全指食品'
        '华夏上证50交易型开放式指数证券投资基金'       → '上证50'
        '易方达中证港股通内地金融交易型...'             → '中证港股通内地金融'
    """
    s = str(etf_name or "")
    # Strip the matched issuer/manager prefix (if any).
    if matched_alias and s.startswith(matched_alias):
        s = s[len(matched_alias):]
    # Strip the legal suffix (everything from '交易型' onward).
    idx = s.find(_ETF_LEGAL_SUFFIX_MARKER)
    if idx > 0:
        s = s[:idx]
    # Strip trailing qualifiers that sometimes follow the index name.
    for tail in ("(QDII)", "(LOF)", "（QDII）", "（LOF）"):
        if s.endswith(tail):
            s = s[: -len(tail)]
    return s.strip()


# ============================================================================
# JSON load / save
# ============================================================================

def load_json() -> Optional[Dict[str, Any]]:
    """Load sec_classification.json if it exists."""
    if not os.path.isfile(JSON_PATH):
        return None
    try:
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"    [WARN] Failed to load {JSON_PATH}: {e}", flush=True)
        return None


def save_json(state: Dict[str, Any]) -> None:
    """Save sec_classification.json (catalog + indices only — no ETF/stock data)."""
    json_state = {
        "version": 1,
        "built_at": datetime.datetime.now().isoformat(),
        "csv_source": state.get("csv_source", ""),
        "catalog": state["catalog"],
        "indices": state["indices"],
    }
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(json_state, f, ensure_ascii=False, indent=2)
    print(f"    [JSON] Saved {JSON_PATH} "
          f"({len(json_state['indices'])} indices, catalog only)", flush=True)


# ============================================================================
# DB queries
# ============================================================================

async def fetch_stock_index_mapping(conn) -> Dict[str, List[Tuple[str, float]]]:
    """For each stock, find ALL indexes where the stock's weight > 2%.

    Uses the LATEST composition snapshot per index. BROAD-sector indices are
    NOT filtered here (the caller filters them using the in-memory ``indices``
    dict, which knows each index's sector_id). Returns:
        { stock_code: [(index_code, weight_pct), ...] }
    """
    rows = await conn.fetch("""
        WITH latest AS (
            SELECT code, MAX(snapshot_date) AS max_date
              FROM stats.sec_composition
             WHERE source_type = 'index' AND stock_code IS NOT NULL
             GROUP BY code
        )
        SELECT sc.stock_code, sc.code AS index_code, sc.weight_pct
          FROM stats.sec_composition sc
          JOIN latest ld ON sc.code = ld.code AND sc.snapshot_date = ld.max_date
         WHERE sc.source_type = 'index' AND sc.stock_code IS NOT NULL
           AND sc.weight_pct > $1::numeric
         ORDER BY sc.stock_code, sc.weight_pct DESC, sc.code
    """, STOCK_WEIGHT_THRESHOLD)

    result: Dict[str, List[Tuple[str, float]]] = {}
    for r in rows:
        result.setdefault(r["stock_code"], []).append(
            (r["index_code"], float(r["weight_pct"])))
    return result


async def fetch_index_meta(conn) -> Dict[str, Dict[str, Any]]:
    """Fetch index names + coverage from index_identity.

    Returns: { code: { "name": ..., "n_days": ..., "first_date": ..., "last_date": ... } }
    """
    rows = await conn.fetch("""
        SELECT code,
               MAX(name) AS name,
               COUNT(*)   AS n_days,
               MIN(date)::text AS first_date,
               MAX(date)::text AS last_date
          FROM stats.index_identity
         GROUP BY code
    """)
    return {
        r["code"]: {
            "name": r["name"] or "",
            "n_days": int(r["n_days"]),
            "first_date": r["first_date"],
            "last_date": r["last_date"],
        }
        for r in rows
    }


async def fetch_stock_meta(conn) -> Dict[str, Dict[str, Any]]:
    """Fetch stock names + coverage from stock_identity (non-NULL close only).

    Returns: { code: { "name": ..., "n_days": ..., "first_date": ..., "last_date": ... } }
    """
    rows = await conn.fetch("""
        SELECT si.code,
               MAX(si.name) AS name,
               COUNT(*)     AS n_days,
               MIN(si.date)::text AS first_date,
               MAX(si.date)::text AS last_date
          FROM stats.stock_identity si
          JOIN stats.stock_basic_stats b ON si.date = b.date AND si.code = b.code
         WHERE b.close IS NOT NULL
         GROUP BY si.code
    """)
    return {
        r["code"]: {
            "name": r["name"] or "",
            "n_days": int(r["n_days"]),
            "first_date": r["first_date"],
            "last_date": r["last_date"],
        }
        for r in rows
    }


def _exchange_from_code(code: str) -> Optional[str]:
    """Derive exchange from the suffix in a stock code."""
    if code.endswith(".SZ"):
        return "SZ"
    if code.endswith(".SS"):
        return "SS"
    if code.endswith(".BJ"):
        return "BJ"
    if code.endswith(".HK"):
        return "HK"
    return None


# ============================================================================
# Build classification state
# ============================================================================

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

    # --- 1. Classify indices ---
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
            # Use the hand-tuned classification from the JSON cache
            sector_id = prev.get("sector_id", DEFAULT_SECTOR_ID)
            industry_id = prev.get("industry_id", DEFAULT_INDUSTRY_ID)
            # Preserve hand-edited tags; fall back to computing from name
            tags = prev.get("tags")
            if tags is None:
                all_tags = classify_index_tags(name)
                tags = [{"sector_id": t[0], "industry_id": t[2]} for t in all_tags]
            n_from_json += 1
        else:
            # New index OR --reclassify: classify by keyword rules (multi-tag)
            all_tags = classify_index_tags(name)
            sector_id, _, industry_id, _ = all_tags[0]
            tags = [{"sector_id": t[0], "industry_id": t[2]} for t in all_tags]
            if prev is not None:
                n_reclassified += 1
            else:
                n_new_classified += 1
        indices[code] = {
            "name": name,
            "exchange": "HK" if _is_hk_name(name) else None,
            "sector_id": sector_id,
            "industry_id": industry_id,
            "tags": tags,
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
            indices[code] = prev
            n_from_json += 1

    if verbose:
        n_classified = sum(1 for v in indices.values() if v["sector_id"] != DEFAULT_SECTOR_ID)
        n_multi = sum(1 for v in indices.values() if len(v.get("tags", [])) > 1)
        reclass_note = f", {n_reclassified} reclassified" if n_reclassified else ""
        print(f"    [INDICES] {len(indices)} indices "
              f"({n_from_json} from JSON, {n_new_classified} newly classified{reclass_note}, "
              f"{n_classified} matched, {len(indices) - n_classified} → OTHER, "
              f"{n_multi} multi-tag)", flush=True)

    # --- 2. Map ETFs to indices (inherit classification) ---
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
            sector_id = idx["sector_id"]
            industry_id = idx["industry_id"]
        else:
            sector_id = DEFAULT_SECTOR_ID
            industry_id = DEFAULT_INDUSTRY_ID
        # Fallback: if parent index is unclassified (OTHER), try classifying
        # the ETF by its own name (or index_name for IB names).
        if sector_id == DEFAULT_SECTOR_ID:
            idx_name = idx["name"] if idx is not None else r.get("index_name", "")
            name_sector, _, name_industry, _ = classify_etf_by_name(
                etf_name, idx_name)
            if name_sector != DEFAULT_SECTOR_ID:
                sector_id = name_sector
                industry_id = name_industry
                n_name_classified += 1
        # Override exchange to 'HK' when the ETF's underlying is HK-listed,
        # detected via ETF name or parent index name.  This applies even when
        # the ETF itself is listed on SH/SZ (e.g. 513050.SS tracking 港股通互联).
        if exchange != "HK":
            idx_name = idx["name"] if idx is not None else r.get("index_name", "")
            if _is_hk_name(etf_name) or _is_hk_name(idx_name):
                exchange = "HK"

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
            is_primary = True  # cleaned == matched index name by definition
            n_overwritten += 1

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

    # --- 3. Map stocks to indices (all qualifying, weight > 2%, excl. BROAD) ---
    # Each stock produces ONE ROW PER qualifying index (multiple rows allowed).
    # BROAD-sector indices (e.g. CSI 300, SSE Composite) are excluded because
    # they convey no industry information. Indices not in the `indices` dict
    # (unclassified) are included — the caller can classify them via JSON later.
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
        # Filter out BROAD-sector indices (known in `indices` dict)
        qualifying = [
            (idx_code, idx_weight)
            for idx_code, idx_weight in mappings
            if idx_code not in indices
            or indices[idx_code]["sector_id"] != "BROAD"
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
                    sector_id = idx["sector_id"]
                    industry_id = idx["industry_id"]
                else:
                    sector_id = DEFAULT_SECTOR_ID
                    industry_id = DEFAULT_INDUSTRY_ID
                # Inherit HK exchange from parent index when the stock is held
                # by a HK-themed index (港股通, 恒生, etc.).  HK-listed stocks
                # lack the .SZ/.SS/.BJ suffix so _exchange_from_code returns
                # None; this override ensures they are labelled correctly.
                stock_exchange = exchange
                if idx is not None and idx.get("exchange") == "HK":
                    stock_exchange = "HK"
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
                    "n_days": meta["n_days"],
                    "first_date": meta["first_date"],
                    "last_date": meta["last_date"],
                    # Stock owners (listed company names) are not curated in
                    # sec_owners.json yet — leave NULL until a company-name
                    # source is wired in.
                    "owner_id": None,
                })
        else:
            stocks.append({
                "code": stock_code,
                "name": meta["name"],
                "exchange": exchange,
                "parent_index_code": "",
                "parent_index_weight": None,
                "parent_index_is_primary": False,
                "sector_id": DEFAULT_SECTOR_ID,
                "industry_id": DEFAULT_INDUSTRY_ID,
                "n_days": meta["n_days"],
                "first_date": meta["first_date"],
                "last_date": meta["last_date"],
                "owner_id": None,
            })

    if verbose:
        n_stock_codes = len(set(s["code"] for s in stocks))
        n_mapped = sum(1 for s in stocks if s["parent_index_code"])
        n_other = sum(1 for s in stocks if not s["parent_index_code"])
        n_primary = sum(1 for s in stocks if s["parent_index_is_primary"])
        print(f"    [STOCKS] {n_stock_codes} stocks → {len(stocks)} rows "
              f"({n_mapped} mapped to index, {n_other} → OTHER, "
              f"{n_primary} primary)", flush=True)

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


# ============================================================================
# DB upsert
# ============================================================================

async def upsert_to_db(conn, state: Dict[str, Any], verbose: bool = True,
                       force: bool = False):
    """Upsert the classification state to stats.sec_classification + stats.sec_index_tags.

    Labels (sector_label, industry_label, industry_slug) are DENORMALIZED
    onto every sec_classification row by looking them up from the in-memory
    catalog at upsert time.  No separate catalog table is needed — the
    former stats.sec_sector_industry_map has been DROPPED.

    ``force`` — when True, truncates stats.sec_classification entirely before
    upserting, removing stale rows (e.g. ETFs no longer in the CSV, indices
    no longer in the JSON).  When False (default), existing index/ETF rows
    are upserted in place (stale rows preserved).  sec_index_tags and
    sec_owners are always truncated + rebuilt (needed for correctness when
    JSON is hand-edited).  Stock rows are always DELETEd + re-inserted
    (the set of qualifying indices can change between runs).
    """
    catalog = state["catalog"]

    # --- 0. Force mode: truncate sec_classification to remove stale rows ---
    if force:
        if verbose:
            print(f"    [DB] Force mode: truncating stats.sec_classification...",
                  flush=True)
        await truncate_table_async(conn, "stats.sec_classification")

    # --- 0. Migrate old NULL parent_index_code → '' (new NOT NULL schema) ---
    # Idempotent: no-op once all rows have been migrated.
    await conn.execute(
        "UPDATE stats.sec_classification SET parent_index_code = '' "
        "WHERE parent_index_code IS NULL"
    )

    # --- 0b. Upsert owners (stats.sec_owners) ---
    # Must run BEFORE sec_classification so the logical owner_id reference is
    # always valid.  Truncate + rebuild each run keeps the table in sync with
    # the curated sec_owners.json (stale entries removed when JSON is edited).
    owner_rows: List[Dict[str, Any]] = []
    for o in state.get("owners", []):
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

    # --- 1. Upsert indices (type='index') ---
    # parent_index_code = '' (root of hierarchy); PK is (code, parent_index_code).
    # Indices have no parent and no owner → parent_index_is_primary=FALSE,
    # owner_id=NULL.
    index_rows: List[Dict[str, Any]] = []
    for code, v in state["indices"].items():
        fd = v.get("first_date")
        ld = v.get("last_date")
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
            "n_days": v.get("n_days", 0),
            "first_date": _parse_date(fd),
            "last_date": _parse_date(ld),
            "parent_index_code": "",
            "parent_index_weight": None,
            "parent_index_is_primary": False,
            "owner_id": None,
        })
    if index_rows:
        inserted = await bulk_upsert_async(
            conn, "stats.sec_classification", index_rows,
            ["code", "parent_index_code"])
        if verbose:
            print(f"    [DB] Upserted {inserted:,} index rows into "
                  f"stats.sec_classification", flush=True)

    # --- 1b. Upsert index tags (sec_index_tags) ---
    # Stores ALL classifications per index (multi-tag). Truncate + rebuild
    # each run so stale tags are removed when the JSON is hand-edited.
    # is_broad_market = TRUE ONLY for the PRIMARY tag (tags[0]) when its
    # sector_id == 'BROAD'.  Secondary BROAD tags (e.g. "中证银行" gets
    # BANKS primary + BROAD_CSI secondary) do NOT count — only indices
    # whose PRIMARY classification is a market-board index are broad-market.
    # This is the SINGLE source of truth for broad-market status; callers
    # (incl. the PerfAttr API) JOIN this table instead of reading a column
    # on sec_classification to avoid duplication.
    tag_rows: List[Dict[str, Any]] = []
    for code, v in state["indices"].items():
        tags = v.get("tags")
        if not tags:
            tags = [{"sector_id": v["sector_id"], "industry_id": v["industry_id"]}]
        for i, tag in enumerate(tags):
            tag_rows.append({
                "code": code,
                "sector_id": tag["sector_id"],
                "industry_id": tag["industry_id"],
                "is_broad_market": i == 0 and tag["sector_id"] == "BROAD",
            })
    if tag_rows:
        await truncate_table_async(conn, "stats.sec_index_tags")
        inserted = await bulk_upsert_async(
            conn, "stats.sec_index_tags", tag_rows,
            ["code", "sector_id", "industry_id"])
        if verbose:
            print(f"    [DB] Inserted {inserted:,} index tag rows into "
                  f"stats.sec_index_tags", flush=True)

    # --- 2. Upsert ETFs (type='etf') ---
    # One-to-one ETF → tracking index; PK is (code, parent_index_code).
    # ETF broad-market status is derived on demand via the parent_index_code
    # → sec_index_tags JOIN (no denormalized column on sec_classification).
    etf_rows: List[Dict[str, Any]] = []
    for code, v in state["etfs"].items():
        sector_label, industry_label, industry_slug = _lookup_labels(
            catalog, v["sector_id"], v["industry_id"])
        etf_rows.append({
            "code": code,
            "name": v["name"],
            "type": "etf",
            "exchange": v["exchange"],
            "sector_id": v["sector_id"],
            "sector_label": sector_label,
            "industry_id": v["industry_id"],
            "industry_label": industry_label,
            "industry_slug": industry_slug,
            "parent_index_code": v["parent_index_code"],
            "parent_index_weight": None,
            "parent_index_is_primary": v.get("parent_index_is_primary", False),
            "aum_yi": v.get("aum_yi"),
            "owner_id": v.get("owner_id"),
        })
    if etf_rows:
        inserted = await bulk_upsert_async(
            conn, "stats.sec_classification", etf_rows,
            ["code", "parent_index_code"])
        if verbose:
            print(f"    [DB] Upserted {inserted:,} ETF rows into "
                  f"stats.sec_classification", flush=True)

    # --- 3. Upsert stocks (type='stock') ---
    # Stocks can have MULTIPLE rows (one per qualifying index). Delete all
    # existing stock rows first to avoid stale entries when the set of
    # qualifying indexes changes between runs.
    await conn.execute("DELETE FROM stats.sec_classification WHERE type = 'stock'")

    stock_rows: List[Dict[str, Any]] = []
    for v in state["stocks"]:
        fd = v.get("first_date")
        ld = v.get("last_date")
        sector_label, industry_label, industry_slug = _lookup_labels(
            catalog, v["sector_id"], v["industry_id"])
        stock_rows.append({
            "code": v["code"],
            "name": v["name"],
            "type": "stock",
            "exchange": v["exchange"],
            "sector_id": v["sector_id"],
            "sector_label": sector_label,
            "industry_id": v["industry_id"],
            "industry_label": industry_label,
            "industry_slug": industry_slug,
            "n_days": v.get("n_days", 0),
            "first_date": _parse_date(fd),
            "last_date": _parse_date(ld),
            "parent_index_code": v["parent_index_code"],
            "parent_index_weight": v["parent_index_weight"],
            "parent_index_is_primary": v.get("parent_index_is_primary", False),
            "owner_id": v.get("owner_id"),
        })
    if stock_rows:
        inserted = await bulk_upsert_async(
            conn, "stats.sec_classification", stock_rows,
            ["code", "parent_index_code"])
        if verbose:
            print(f"    [DB] Upserted {inserted:,} stock rows into "
                  f"stats.sec_classification", flush=True)


def _parse_date(s: Optional[str]) -> Optional[datetime.date]:
    """Parse a YYYY-MM-DD string to datetime.date (None if blank/invalid)."""
    if not s:
        return None
    try:
        return datetime.datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _lookup_labels(
    catalog: Dict[str, Any],
    sector_id: str,
    industry_id: str,
) -> Tuple[str, str, str]:
    """Look up (sector_label, industry_label, industry_slug) from the catalog.

    Used to denormalize labels onto every sec_classification row at upsert
    time so frontend services can render labels without JOINing a catalog
    table.  Falls back to the DEFAULT_* constants when the sector_id or
    industry_id is not present in the catalog (e.g. legacy 'OTHER' entries).
    """
    sector = catalog.get(sector_id)
    if sector is None:
        return (DEFAULT_SECTOR_LABEL, DEFAULT_INDUSTRY_LABEL,
                DEFAULT_INDUSTRY_ID.lower())
    industry = sector["industries"].get(industry_id)
    if industry is None:
        return (sector["label"], DEFAULT_INDUSTRY_LABEL,
                DEFAULT_INDUSTRY_ID.lower())
    return (sector["label"], industry["label"], industry["slug"])


# ============================================================================
# Main
# ============================================================================

async def main():
    ap = argparse.ArgumentParser(
        description="Build two-level security classification (sector → industry)."
    )
    ap.add_argument("--no-db", action="store_true",
                    help="Skip DB upsert (load JSON + recompute ETFs/stocks + save JSON only)")
    ap.add_argument("--reclassify", action="store_true",
                    help="Force reclassification of ALL indices from keyword rules "
                         "(ignores stale JSON-cached sector_id/industry_id/tags). "
                         "Use this after changing INDEX_RULES to propagate new rules.")
    add_force_arg(ap)
    args = ap.parse_args()

    t0 = datetime.datetime.now()
    mode_parts = []
    if args.no_db:
        mode_parts.append("no-db")
    if args.force:
        mode_parts.append("FORCE (truncate + recompute)")
    else:
        mode_parts.append("incremental (upsert)")
    if args.reclassify:
        mode_parts.append("reclassify")
    print_build_header(
        "BUILD CLASSIFICATION  ·  sector → industry  ·  ETF + Index + Stock",
        **{
            "JSON path": JSON_PATH,
            "Today": TODAY_STR,
            "Mode": " + ".join(mode_parts),
        }
    )

    # --- Load JSON (index classifications — the authoritative source) ---
    prev_state = load_json()
    if prev_state:
        print(f"    [JSON] Loaded index classifications: "
              f"{len(prev_state.get('indices', {}))} indices", flush=True)
    else:
        print(f"    [JSON] No existing JSON — all indices will be classified by keyword rules",
              flush=True)

    # --- Load owners (sec_owners.json — curated ETF manager / company registry) ---
    owners = load_owners()

    # --- Load CSV (ETF → index mapping) ---
    csv_path = find_latest_csv()
    if csv_path is None:
        print(f"    [FATAL] No etf_index_map_*.csv found in {CSV_DIR}", flush=True)
        sys.exit(1)
    print(f"    [CSV] Loading ETF → index mapping: {os.path.basename(csv_path)}", flush=True)
    etf_rows = load_etf_index_csv(csv_path)
    print(f"    [CSV] {len(etf_rows)} ETF → index mappings loaded", flush=True)

    # --- Connect to DB (for index meta, stock mapping, and upsert) ---
    conn = None
    if not args.no_db:
        print("\n[1/2] Connecting to database …", flush=True)
        conn = await get_db_or_exit()

    try:
        print("\n[2/2] Building classification …", flush=True)
        state = await build_classification(
            conn, etf_rows, prev_state=prev_state, owners=owners, verbose=True,
            reclassify_indices=args.reclassify)

        # --- Save JSON (indices only) ---
        save_json(state)

        # --- Summary ---
        print(f"\n    Summary:", flush=True)
        print(f"      Catalog : {len(state['catalog'])} sectors", flush=True)
        print(f"      Indices : {len(state.get('indices', {}))}", flush=True)
        print(f"      ETFs    : {len(state.get('etfs', {}))}", flush=True)
        print(f"      Stocks  : {len(state.get('stocks', []))} rows "
              f"({len(set(s['code'] for s in state.get('stocks', [])))} codes)", flush=True)
        print(f"      Owners  : {len(state.get('owners', []))}", flush=True)

        # --- Upsert to DB ---
        if conn is not None:
            print("\n[DB] Upserting to database …", flush=True)
            await upsert_to_db(conn, state, verbose=True, force=args.force)
    finally:
        if conn is not None:
            await conn.close()

    elapsed = (datetime.datetime.now() - t0).total_seconds()
    print(f"\n  Wall time: {elapsed:.1f}s", flush=True)
    print("=" * 78, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
