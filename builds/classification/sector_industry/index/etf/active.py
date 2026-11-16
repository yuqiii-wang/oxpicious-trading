"""Classify the curated ACTIVE funds (主动管理型 + 指数增强型 ETFs).

The strategy sector ``ACTIVE`` (主动) is registry-driven, NOT
keyword-driven: active funds are actively managed, so their names carry no
stable keyword (增强/主动 appear in many passive-adjacent names, and pure
active ETFs are named like 品质均衡/价值精选).  The hand-curated registry
``database/sec_statics/active_funds/*.json`` is the only correct source —
its empty-keyword rules in ``classification_rules/strategy.json``
(ACTIVE_ENHANCED / ACTIVE_MGMT) exist purely so the catalog and the
strategy-sector list carry the vocabulary.

This overlay runs AFTER classify_etfs / unmatched-funds / sec_info retry
(all keyword paths) and BEFORE create_dummy_indices, so:

  * an active ETF that entered ``etfs`` through any keyword path (CSV
    inheritance, v_etf_margin name classification, sec_info retry) keeps
    its name/parent/owner and gets its PRIMARY pair overwritten to
    (ACTIVE, <industry by style>) with is_industry_not_strategy=FALSE;
  * an active ETF absent from ``etfs`` entirely (too new for the CSV and
    for v_etf_margin — e.g. the 2026-10 SSE 581xxx 主动管理型ETF first
    batch) is ADDED with the registry's name/exchange/owner/aum and an
    empty parent — the following create_dummy_indices then gives it a
    DUMMY_ACTIVE_* parent so every ETF keeps a non-empty parent.

OTC registry entries never enter classification (classification is "for
etf only" — OTC funds have no listing venue and sec_classification only
accepts real venues).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from database.sec_statics.active_funds_store import (
    STYLE_ACTIVE_MGMT,
    STYLE_ENHANCED_INDEX,
    VENUE_ETF,
    ActiveFund,
    active_funds_by_owner,
)

import logging
logger = logging.getLogger(__name__)

# Strategy sector + theme (industry) per registry style — mirrors the
# empty-keyword rules in classification_rules/strategy.json.
ACTIVE_SECTOR_ID = "ACTIVE"
STYLE_TO_INDUSTRY: Dict[str, str] = {
    STYLE_ENHANCED_INDEX: "ACTIVE_ENHANCED",   # 指数增强
    STYLE_ACTIVE_MGMT: "ACTIVE_MGMT",          # 主动管理
}


class ActiveFundClassifier:
    """Overlay the active-funds registry onto the ``etfs`` state dict.

    ``funds_by_owner`` is injectable ({owner_id: [funds]} — tests or a
    narrowed selection); the default loads every owner file via
    :func:`active_funds_by_owner`.  Only ``venue=etf`` entries
    participate; the OTC entries of the registry are curated reference
    data with no classification consumer.
    """

    def __init__(
        self,
        funds_by_owner: Optional[Dict[str, List[ActiveFund]]] = None,
    ) -> None:
        self._funds_by_owner: Dict[str, List[ActiveFund]] = (
            funds_by_owner if funds_by_owner is not None
            else active_funds_by_owner())

    def _industry_of(self, fund: ActiveFund) -> Optional[str]:
        """Theme industry for one fund's style; None when unmappable."""
        industry = STYLE_TO_INDUSTRY.get(fund.style)
        if industry is None:
            logger.warning("[ACTIVE] fund %s (%s) carries unknown style %r "
                           "— skipped", fund.code, fund.name, fund.style)
        return industry

    def _listed_funds(self) -> List[Tuple[str, ActiveFund]]:
        """(owner_id, fund) for every venue=etf registry entry."""
        out: List[Tuple[str, ActiveFund]] = []
        for owner_id, funds in sorted(self._funds_by_owner.items()):
            out.extend((owner_id, f) for f in funds if f.venue == VENUE_ETF)
        return out

    def classify(self, etfs: Dict[str, Any], verbose: bool = True) -> Dict[str, Any]:
        """Mutates and returns ``etfs`` — override-or-add each listed
        active ETF to (ACTIVE, <theme by style>), is_industry_not_strategy
        FALSE."""
        n_overridden = 0
        n_added = 0

        for owner_id, fund in self._listed_funds():
            industry_id = self._industry_of(fund)
            if industry_id is None:
                continue

            existing = etfs.get(fund.code)
            if existing is not None:
                existing["sector_id"] = ACTIVE_SECTOR_ID
                existing["industry_id"] = industry_id
                existing["is_industry_not_strategy"] = False
                # name/exchange/parent stay as the keyword path produced
                # them (exchange trading names are the UI convention for
                # listed ETFs); owner/aum are FILLED from the registry
                # when the unmatched-funds path left them unset (it
                # matches owners only via the CSV/alias paths).
                if not existing.get("owner_id"):
                    existing["owner_id"] = owner_id
                if existing.get("aum_yi") is None and fund.aum_yi is not None:
                    existing["aum_yi"] = fund.aum_yi
                n_overridden += 1
                continue

            if not fund.exchange:
                logger.warning("[ACTIVE] fund %s (%s) has venue=etf but no "
                               "exchange in the registry — skipped (cannot "
                               "enter sec_classification without a venue)",
                               fund.code, fund.name)
                continue
            etfs[fund.code] = {
                "name": fund.name,
                "exchange": fund.exchange,
                "parent_index_code": "",
                "parent_index_weight": None,
                "parent_index_is_primary": False,
                "sector_id": ACTIVE_SECTOR_ID,
                "industry_id": industry_id,
                "is_industry_not_strategy": False,
                "aum_yi": fund.aum_yi,
                "owner_id": owner_id,
            }
            n_added += 1

        if verbose:
            n_otc = sum(
                1 for funds in self._funds_by_owner.values()
                for f in funds if f.venue != VENUE_ETF)
            logger.info("    [ACTIVE] %d listed active ETFs tagged %s "
                        "(%d overridden, %d added; %d OTC registry funds not "
                        "classified)", n_overridden + n_added,
                        ACTIVE_SECTOR_ID, n_overridden, n_added, n_otc)

        return etfs
