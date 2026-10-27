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

from _common.df_utils import host_array, host_unique
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

    def _txt(chi: str) -> pd.Series:
        """Column as stripped strings; a missing column reads as '' per row
        (the exact ``str(row.get(chi, "")).strip()`` semantics — a NaN cell
        becomes the string 'nan', same as the former per-row str() cast;
        pandas' str dtype astype(str) keeps NaN, so fill it explicitly)."""
        if chi in df.columns:
            return df[chi].fillna("nan").astype(str).str.strip()
        return pd.Series("", index=df.index)

    etf_code = _txt("产品代码")
    index_code = _txt("标的指数代码")
    keep = (etf_code != "") & (index_code != "")

    # 上市地 → exchange via a small dict over the venue values (map(dict)
    # stays on the cudf-native path; a per-row _exchange_from_listed_at
    # call would not). '' sentinel = no venue match (None in the output —
    # Series.map would turn None values into NaN).
    listed_at = _txt("上市地")
    exchange = listed_at.map(
        {v: (_exchange_from_listed_at(v) or "") for v in host_unique(listed_at)}
    )
    # HK-listed ETFs have code format "XXXX HK Equity" — extract the
    # numeric prefix so it can be stored as a bare code with .HK suffix.
    hk = (exchange == "HK") & etf_code.str.contains(" HK", regex=False)
    etf_code = etf_code.where(~hk, etf_code.str.split(" ").str[0])

    # Parse AUM (资产净值 in 亿元): '' → None, non-numeric → None, and a NaN
    # cell → float('nan') — the exact ``float(aum_str) if aum_str else None``
    # per-row semantics (the string 'nan' parses to NaN, it is not empty).
    aum_txt = _txt("资产净值（亿元）")
    aum_yi = pd.to_numeric(aum_txt, errors="coerce").astype(object).where(
        aum_txt != "", None)

    etf_name = _txt("产品名称")
    index_name = _txt("标的指数")
    manager = _txt("管理人")
    return [
        {
            "etf_code": ec,
            "etf_name": en,
            "index_code": ic,
            "index_name": inm,
            "exchange": ex if ex else None,
            "aum_yi": None if a is None else float(a),
            "manager": mg,
        }
        for ec, en, ic, inm, ex, a, mg in zip(
            host_array(etf_code[keep]),
            host_array(etf_name[keep]),
            host_array(index_code[keep]),
            host_array(index_name[keep]),
            host_array(exchange[keep]),
            host_array(aum_yi[keep]),
            host_array(manager[keep]),
        )
    ]
