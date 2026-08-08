"""CSV reading — ETF → index mapping.

Loads the latest ``etf_index_map_*`` CSV produced by the csindex_linked_etf
download script.  Prefers the unfiltered ``etf_index_map_all_YYYY-MM-DD.csv``
(ALL ETFs with AUM data) over the filtered version, since the ``all``
variant is the authoritative source for ETF → index linkage and AUM.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import pandas as pd

from builds.classification.sector_industry.exchange import _exchange_from_listed_at
from builds.classification.sector_industry.paths import CSV_DIR


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
