"""Classify exchange-traded funds NOT in the ETF→index CSV.

The ETF→index CSV (etf_index_map_*.csv) contains only actual ETFs.  However,
``stats.v_etf_margin`` tracks ALL exchange-traded funds — including:

  * Structured/leveraged split-share funds (分级基金, 150xxx.SZ) — A/B shares
    of leveraged funds, mostly delisted after the 2020 regulatory ban.
  * LOF (Listed Open-End Funds, 16xxxx.SZ) — regular open-end funds traded
    on exchanges (e.g. 南方500, 国泰医药, 恒生H股).
  * Other active/主题 funds with exchange codes.

These appear in the etf-margin UI (which reads from v_etf_margin) but have
no classification in sec_classification, so they show as '未分类' (OTHER).
This module fetches their codes+names from v_etf_margin and classifies them
by name using the same keyword rules as ETFs/stocks, then adds them to the
``etfs`` dict with type='etf' so they get upserted to sec_classification.

Only codes NOT already in the ``etfs`` dict are processed — CSV-mapped ETFs
take precedence and are never overwritten.
"""
from __future__ import annotations

from typing import Any, Dict

from _common.build_commons import rec_cols
from _common.sec_statics.classification import (
    DEFAULT_SECTOR_ID,
    DEFAULT_INDUSTRY_ID,
    classify_index,
    classify_index_strategy,
)

from builds.classification.sector_industry.exchange import (
    _is_hk_name,
    _is_overseas_name,
)

# Standalone numeric codes in fund names (e.g. '鹏华300LOF' → '300') that
# should map to broad-market CSI series.  These are too generic for the main
# INDUSTRY_RULES (would false-match stock names containing 300/500), but are
# safe here because this module only processes unmatched funds.
_BROAD_NUMERIC_MAP = {
    "300": ("BROAD", "宽基", "BROAD_CSI300", "沪深300"),
    "500": ("BROAD", "宽基", "BROAD_CSI500", "中证500"),
    "800": ("BROAD", "宽基", "BROAD_CSI800", "中证800"),
    "1000": ("BROAD", "宽基", "BROAD_CSI1000", "中证1000"),
    "2000": ("BROAD", "宽基", "BROAD_CSI2000", "中证2000"),
}


def _classify_fund_by_name(clean_name: str):
    """Classify a fund name, with extra numeric-pattern handling."""
    # Try standard keyword rules first
    result = classify_index(clean_name)
    if result[0] != DEFAULT_SECTOR_ID:
        return result, True  # industry match
    result = classify_index_strategy(clean_name)
    if result[0] != DEFAULT_SECTOR_ID:
        return result, False  # strategy match
    # Try standalone numeric patterns: extract trailing digits from clean_name
    import re
    m = re.search(r"(\d+)$", clean_name)
    if m:
        num = m.group(1)
        if num in _BROAD_NUMERIC_MAP:
            sec, sec_label, ind, ind_label = _BROAD_NUMERIC_MAP[num]
            return (sec, sec_label, ind, ind_label), False
    return (DEFAULT_SECTOR_ID, "其他", DEFAULT_INDUSTRY_ID, "未分类"), True


async def fetch_unmatched_funds(conn) -> list[dict]:
    """Fetch exchange-traded funds from v_etf_margin not yet classified.

    Returns rows with 'code' (e.g. '150008.SZ') and 'name'.
    Only includes codes with >= 40 trading days (matching the UI threshold).
    """
    if conn is None:
        return []
    rows = await conn.fetch(
        """
        SELECT v.code,
               MAX(v.name)     AS name,
               MAX(e.exchange) AS exchange
          FROM stats.v_etf_margin v
          LEFT JOIN stats.etf_identity e ON v.code = e.code
         GROUP BY v.code
        HAVING COUNT(v.date) >= 40
         ORDER BY v.code
        """
    )
    # Whole-column extraction + zip-dict row assembly (no per-row record access).
    # exchange comes straight from the DB column (never derived from suffix).
    cols = rec_cols(rows)
    return [{"code": c, "name": n, "exchange": x}
            for c, n, x in zip(cols["code"], cols["name"], cols["exchange"])]


def classify_unmatched_funds(
    all_funds: list[dict],
    etfs: Dict[str, Any],
    verbose: bool = True,
) -> Dict[str, Any]:
    """Classify funds not already in ``etfs`` by name-based keyword rules.

    Mutates and returns the ``etfs`` dict — adds new entries for unmatched
    funds.  CSV-mapped ETFs are never overwritten.

    Strategy:
      1. For each fund code in all_funds not already in etfs, use its name
         as-is — structured-fund share-class suffixes ('中证100A' →
         '中证100') were already stripped at download-conversion time by
         clean_fund_share_class_names, so builds receive pre-cleaned names.
      2. Classify by industry rules first (classify_index), then strategy
         rules (classify_index_strategy).  Industry takes precedence.
      3. Funds that match neither → sector_id=OTHER (left as-is, but they
         now have a sec_classification row so the UI can at least show them
         with correct name/exchange).
    """
    n_added = 0
    n_classified = 0
    n_other = 0

    for fund in all_funds:
        code = fund["code"]
        if code in etfs:
            continue  # CSV-mapped ETF — skip
        name = fund["name"] or ""
        # Names arrive pre-cleaned: share-class suffixes ('中证100A',
        # '券商A级', ...) were removed during download-CSV conversion.

        # Classify: industry rules first, then strategy rules, then numeric.
        sector_id = DEFAULT_SECTOR_ID
        industry_id = DEFAULT_INDUSTRY_ID
        is_ind = True

        (cls_sector, _, cls_industry, _), cls_is_ind = _classify_fund_by_name(name)
        if cls_sector != DEFAULT_SECTOR_ID:
            sector_id = cls_sector
            industry_id = cls_industry
            is_ind = cls_is_ind
            n_classified += 1
        else:
            n_other += 1

        # Exchange comes from the DB column (stats.etf_identity.exchange,
        # loaded in fetch_unmatched_funds) — never derived from the code
        # suffix.  Then override to HK/OVERSEAS when the fund name indicates
        # a HK or non-Greater-China underlying (e.g. 港股通LOF, 纳指LOF,
        # 标普500).  The sector_id check (done above) is the primary signal
        # for OVERSEAS; the name check is a fallback for funds whose
        # classification fell through to OTHER.
        exchange = fund.get("exchange") or ""
        if sector_id == "OVERSEAS":
            exchange = "OVERSEAS"
        elif _is_hk_name(name):
            exchange = "HK"
        elif _is_overseas_name(name):
            exchange = "OVERSEAS"

        # When exchange is OVERSEAS, reclassify sector_id/industry_id to
        # OVERSEAS using strategy rules.  Same logic as classify.py: ensures
        # cross-border funds (标普LOF, 纳指LOF, ...) are classified under
        # their OVERSEAS sub-industry, not under the themed domestic industry
        # that matched first (e.g., 标普医药 → OVERSEAS_US, not HC/PHARMA_BROAD).
        if exchange == "OVERSEAS" and sector_id != "OVERSEAS":
            strat_sector, _, strat_industry, _ = classify_index_strategy(name)
            if strat_sector == "OVERSEAS":
                sector_id = strat_sector
                industry_id = strat_industry
                is_ind = False

        etfs[code] = {
            "name": name,
            "exchange": exchange,
            "parent_index_code": "",
            "parent_index_weight": None,
            "parent_index_is_primary": False,
            "sector_id": sector_id,
            "industry_id": industry_id,
            "is_industry_not_strategy": is_ind,
            "aum_yi": None,
            "owner_id": None,
        }
        n_added += 1

    if verbose and n_added:
        print(f"    [FUND] {n_added} unmatched funds classified by name "
              f"({n_classified} matched, {n_other} → OTHER)", flush=True)

    return etfs
