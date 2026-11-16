"""Reader for the hand-curated active-funds registry (active_funds/).

The actively-managed funds (主动管理型 + 指数增强型) of the two tracked
issuers — 易方达 (``e_fund``) and 华泰柏瑞 (``pinebridge``) — curated
2026-11-14 by top 资产规模 + longest 成立历史. One JSON file per owner
under ``database/sec_statics/active_funds/`` (schema + curation policy in
that directory's README); this module is its single loader.

Three consumers gate on this registry:

  * ``builds.classification`` — every ``venue=etf`` entry receives the
    EXPLICIT strategy tag ``ACTIVE`` (主动): ``ACTIVE_ENHANCED`` 指数增强
    for ``style=enhanced_index`` / ``ACTIVE_MGMT`` 主动管理 for
    ``style=active_mgmt``. OTC entries are curated reference data only
    (classification is "for etf only" — OTC funds have no listing venue
    and never enter sec_classification).
  * ``downloads.etf.huatai_pb.*`` / ``downloads.etf.efunds.*`` — every
    PER-FUND service (archive / intro / composition / dividends) defaults
    to the owner's ``venue=etf`` codes (:func:`require_active_etf_funds`).
    The whole-family trend services stay ungated.
  * ``builds.etf.feeder_nav`` — the listed-ETF NAV-history backfill loads
    exactly :func:`active_etf_codes` (:func:`load_active_funds` /
    :func:`active_etf_codes` tolerate a missing registry — an empty
    selection; the downloaders use the raising variant so a broken
    registry can never silently widen to an all-funds crawl).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "active_funds"

# Closed entry vocabularies (mirrors the README schema).
STYLE_ENHANCED_INDEX = "enhanced_index"   # 指数增强 / 增强策略
STYLE_ACTIVE_MGMT = "active_mgmt"         # 主动管理
VENUE_ETF = "etf"                         # exchange-listed
VENUE_OTC = "otc"                         # off-exchange open-end


class ActiveFund(BaseModel):
    """One curated fund entry (the JSON file row shape)."""

    code: str
    name: str
    style: str                    # STYLE_ENHANCED_INDEX | STYLE_ACTIVE_MGMT
    venue: str                    # VENUE_ETF | VENUE_OTC
    inception: str                # ISO date
    exchange: Optional[str] = None  # listing venue for venue=etf (SS/SZ); None for OTC
    aum_yi: Optional[float] = None
    benchmark: Optional[str] = None
    share_classes: List[str] = Field(default_factory=list)
    note: Optional[str] = None


class ActiveOwnerFile(BaseModel):
    """One owner file (active_funds/<owner_id>.json)."""

    owner_id: str
    funds: List[ActiveFund] = Field(default_factory=list)


def owner_path(owner_id: str) -> Path:
    """Path of one owner's registry file."""
    return DATA_DIR / f"{owner_id}.json"


def _read_owner_file(owner_id: str) -> Optional[ActiveOwnerFile]:
    """Parse one owner file; None (with a warning) when missing/unreadable.

    A curation gap must not fail the tolerant consumers (classification
    overlay, feeder_nav filter) — they treat it as an empty selection.
    """
    path = owner_path(owner_id)
    if not path.is_file():
        logger.warning("active-funds registry file missing: %s "
                       "(owner %s yields no funds)", path.name, owner_id)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ActiveOwnerFile.model_validate(data)
    except (OSError, ValueError) as exc:
        logger.warning("active-funds registry file unreadable (%s): %s",
                       path.name, exc)
        return None


def load_active_funds(owner_id: Optional[str] = None) -> List[ActiveFund]:
    """Curated active funds of one owner (or every owner when None).

    Missing files yield an empty list — callers gate their own behaviour
    (downloaders use :func:`require_active_etf_funds` instead).
    """
    if owner_id is not None:
        parsed = _read_owner_file(owner_id)
        return list(parsed.funds) if parsed else []
    out: List[ActiveFund] = []
    for path in sorted(DATA_DIR.glob("*.json")):
        parsed = _read_owner_file(path.stem)
        if parsed:
            out.extend(parsed.funds)
    return out


def active_funds_by_owner() -> Dict[str, List[ActiveFund]]:
    """{owner_id: [funds, …]} across every owner file (venue mixed)."""
    out: Dict[str, List[ActiveFund]] = {}
    for path in sorted(DATA_DIR.glob("*.json")):
        parsed = _read_owner_file(path.stem)
        if parsed:
            out[parsed.owner_id] = list(parsed.funds)
    return out


def active_etf_codes_by_owner() -> Dict[str, List[str]]:
    """{owner_id: [listed active-ETF codes, …]} across every owner file."""
    out: Dict[str, List[str]] = {}
    for path in sorted(DATA_DIR.glob("*.json")):
        parsed = _read_owner_file(path.stem)
        if parsed:
            out[parsed.owner_id] = [f.code for f in parsed.funds
                                    if f.venue == VENUE_ETF]
    return out


def active_etf_codes() -> Set[str]:
    """Every listed active-ETF code (bare 6-digit) across all owners —
    the feeder_nav listed-backfill universe."""
    return {f.code for f in load_active_funds() if f.venue == VENUE_ETF}


def require_active_etf_funds(owner_id: str) -> Tuple[ActiveFund, ...]:
    """The owner's listed active ETFs, RAISING on a missing/empty registry.

    The per-fund download services' default universe — a broken registry
    must fail loudly (never silently fall back to an all-funds crawl).
    """
    parsed = _read_owner_file(owner_id)
    if parsed is None:
        raise RuntimeError(
            f"active-funds registry unreadable for owner {owner_id} "
            f"({owner_path(owner_id)}) — per-fund downloads refuse to run "
            f"without their curated universe")
    codes = tuple(f for f in parsed.funds if f.venue == VENUE_ETF)
    if not codes:
        raise RuntimeError(
            f"active-funds registry has no venue=etf entries for owner "
            f"{owner_id} ({owner_path(owner_id)})")
    return codes
