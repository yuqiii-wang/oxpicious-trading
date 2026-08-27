"""Exchange / listing-venue helpers shared across index, ETF and stock leaves.

The classification carries an ``exchange`` on every row.  Derivation rules:

  INDICES (bare 6-digit or letter-prefix codes, NO .SS/.SZ suffix):
    - DUMMY_* synthetic indices                          -> None
    - sector_id OVERSEAS (primary or any tag)            -> OVERSEAS
    - HK name keywords (港股通/恒生/...) OR H/CES prefix -> HK
    - code prefix 399 (深证/国证)                        -> SZ
    - code prefix 899 (北证)                             -> BJ
    - code prefix 000/930/931/932/950/990 (上证/中证)    -> SS

  ETFs (code WITH .SS/.SZ/.BJ suffix):
    - listing venue from CSV 上市地 or the DB exchange column
    - override to HK when ETF/parent name matches HK keywords
      (港股通/恒生/沪港深/SHS/港美/沪深港/恒生科技/恒生互联网/...)
    - override to OVERSEAS when ETF's primary sector_id is OVERSEAS
      OR name matches overseas keywords
      (标普/纳斯达克/美国/日经/中概/海外中国/全球中国/金砖/中韩/...)

  STOCKS / UNMATCHED FUNDS (suffixed codes):
    - stats.stock_identity.exchange / stats.etf_identity.exchange DB column
      directly (loaded by each leaf's fetch query; never derived from suffix).
      A-share stocks held by HK/overseas-themed indices KEEP their own
      exchange (they are still A-share listed).

  is_primary_exchange (derived, never set manually — see _is_primary_exchange):
    SS/STAR/SZ/GEM/BJ  -> True   (Greater-China primary)
    HK/OVERSEAS        -> False  (cross-border / non-Greater-China)
    None               -> None   (synthetic DUMMY indices)
    Persisted as a post-upsert UPDATE in sector_industry/upsert.py.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Keywords in security/index/ETF names that indicate the underlying stocks are
# Hong Kong-listed.  "中华" alone is NOT included — "中华半导体芯片" tracks
# A-shares; HK variants like "中华港股通精选100" are already caught by "港股".
_HK_NAME_KEYWORDS: List[str] = [
    "港股通", "港股", "香港", "恒生", "恒指",
    "H股", "红筹", "沪港深", "深港沪", "SHS",
    # Compound cross-border keywords that include HK (港) — per user direction,
    # 港美 and 沪深港 are HK-tier (Greater-China border) not OVERSEAS.
    "港美", "沪深港",
    # 恒生-themed sub-indices (恒生科技/恒生互联网/恒生医疗/...) — HK-listed.
    "恒生科技", "恒生互联网",
]

# Keywords in names that indicate non-Greater-China (overseas) underlying.
# Used as a fallback when sector_id is not yet computed; the primary signal
# is sector_id == 'OVERSEAS' (set by the OVERSEAS strategy rules in
# _common.sec_statics.classification).  Kept intentionally specific to avoid
# false-matching stock company names (戎美股份→美股, 亚太实业→亚太).
#
# NOTE: "中欧" is deliberately NOT included — "中欧基金" is a Chinese fund
# manager (166xxx LOF series: 中欧趋势/成长/强债/...) tracking A-shares, NOT
# Europe-themed.  Only "中欧趋势" (the overseas strategy index) would qualify,
# and that is already caught by sector_id == 'OVERSEAS'.
_OVERSEAS_NAME_KEYWORDS: List[str] = [
    "纳斯达克", "纳指", "标普", "道琼斯", "美国",
    "日经", "日本",
    "德国", "法国", "欧洲",
    "巴西", "沙特", "印度", "越南",
    "亚太精选",
    # China-concept / overseas-China internet QDII ETFs (中概互联, 海外中国互联,
    # 全球中国互联).  These track US/HK-listed China-concept stocks — cross-border.
    "中概", "海外中国", "全球中国",
    # BRICs / Korea cross-border QDII ETFs (招商金砖, 中韩半导体).
    "金砖", "中韩",
]


def _is_hk_name(name: str) -> bool:
    """Return True if the name indicates Hong Kong-listed underlying stocks."""
    s = str(name)
    return any(kw in s for kw in _HK_NAME_KEYWORDS)


def _is_overseas_name(name: str) -> bool:
    """Return True if the name indicates non-Greater-China (overseas) underlying."""
    s = str(name)
    return any(kw in s for kw in _OVERSEAS_NAME_KEYWORDS)


# Greater-China primary exchanges (mainland listing boards).  Everything
# with one of these `exchange` values is is_primary_exchange=TRUE.
_PRIMARY_EXCHANGES = frozenset({"SS", "STAR", "SZ", "GEM", "BJ"})

# Cross-border / non-Greater-China exchanges → is_primary_exchange=FALSE.
_CROSS_BORDER_EXCHANGES = frozenset({"HK", "OVERSEAS"})


def _is_primary_exchange(exchange: Optional[str]) -> Optional[bool]:
    """Classify an exchange as primary (Greater-China) vs cross-border.

    This is the single source of truth for the ``is_primary_exchange``
    derived column on stats.sec_classification.  The DB column is
    re-derived from ``exchange`` by a post-upsert UPDATE in
    sector_industry/upsert.py (same pattern as ``is_active``), so this
    helper exists for in-memory consistency and documentation only.

      SS / STAR / SZ / GEM / BJ  -> True   (Greater-China primary)
      HK / OVERSEAS              -> False  (cross-border / non-Greater-China)
      None / unrecognized        -> None   (synthetic DUMMY indices)
    """
    if exchange in _PRIMARY_EXCHANGES:
        return True
    if exchange in _CROSS_BORDER_EXCHANGES:
        return False
    return None


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


def _exchange_from_index_code(
    code: str,
    name: str,
    sector_id: Optional[str] = None,
    tags: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    """Derive exchange for an INDEX from its code prefix, name, and classification.

    Indices use bare 6-digit codes (000300, 399001, 930050) or letter-prefix
    codes (H30007, CES100) — they do NOT carry a .SS/.SZ suffix.  The exchange
    is therefore derived from the code prefix, with name/sector overrides for
    HK-themed and overseas-themed indices.

    Priority (first match wins):
      1. DUMMY_* synthetic indices             -> None
      2. sector_id OVERSEAS (primary or tag)   -> OVERSEAS
      3. HK name keywords (港股通/恒生/沪港深/SHS/港美/沪深港/...)  -> HK
      4. Overseas name keywords (标普/纳斯达克/中概/海外中国/全球中国/金砖/中韩/...) -> OVERSEAS
      5. HK code prefix (H*/CES*)              -> HK
      6. SZSE code prefix (399)                -> SZ
      7. BSE code prefix (899)                 -> BJ
      8. SSE/CSI code prefix (000/930/931/932/950/990) -> SS
      9. Unrecognized                          -> None
    """
    # 1. DUMMY indices are synthetic — no exchange.
    if code.startswith("DUMMY_"):
        return None

    # 2. OVERSEAS sector (primary or any tag) — cross-border QDII indices.
    if sector_id == "OVERSEAS" or any(
        t.get("sector_id") == "OVERSEAS" for t in (tags or [])
    ):
        return "OVERSEAS"

    # 3. HK name keywords (港股通, 恒生, 沪港深, SHS, 港美, 沪深港, ...).
    if _is_hk_name(name):
        return "HK"

    # 4. Overseas name keywords (标普, 纳斯达克, 中概, 海外中国, 全球中国,
    #    金砖, 中韩, ...).  Some CSI indices (930796 全球中国互联网,
    #    931790 中韩半导体) have 930xxx/931xxx codes that default to SS
    #    but actually track cross-border QDII targets — the name override
    #    catches them before the code-prefix rules below.
    if _is_overseas_name(name):
        return "OVERSEAS"

    # 5. HK code prefixes: CSI HK indices (H00xxx, H11xxx, H30xxx, H50xxx)
    #    and CES100 (中华港股通精选100).
    if code.startswith(("H", "CES")):
        return "HK"

    # 6. SZSE indices (399xxx — 深证/国证 series).
    if code.startswith("399"):
        return "SZ"

    # 7. BSE indices (899xxx — 北证 series).
    if code.startswith("899"):
        return "BJ"

    # 8. SSE/CSI indices:
    #    000xxx — 上证 series + legacy CSI codes (沪深300, 上证50)
    #    930xxx, 931xxx, 932xxx — CSI (中证) newer codes
    #    950xxx — 上证 bond/strategy series
    #    990xxx — 中华 series (cross-listed, default to SS)
    if code.startswith(("000", "930", "931", "932", "950", "990")):
        return "SS"

    # 9. Unrecognized code pattern.
    return None
