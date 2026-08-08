"""Exchange / listing-venue helpers shared across index, ETF and stock leaves.

The classification carries an ``exchange`` on every row.  Deriving it is
spread across three sources:
  * 上市地 (CSV column for ETFs)            -> _exchange_from_listed_at
  * stock code suffix (.SS/.SZ/.BJ/.HK)     -> _exchange_from_code
  * HK-themed names (港股通/恒生/沪港深/...)  -> _is_hk_name

The HK-name check is name-based because HK-themed ETFs are often listed on
SH/SZ (so 上市地 is 上海/深圳) while their holdings are HK stocks — the
ETF/index/stock exchange is overridden to 'HK' when the name matches.
"""
from __future__ import annotations

from typing import List, Optional

# Keywords in security/index/ETF names that indicate the underlying stocks are
# Hong Kong-listed.  "中华" alone is NOT included — "中华半导体芯片" tracks
# A-shares; HK variants like "中华港股通精选100" are already caught by "港股".
_HK_NAME_KEYWORDS: List[str] = [
    "港股通", "港股", "香港", "恒生", "恒指",
    "H股", "红筹", "沪港深", "深港沪", "SHS",
]


def _is_hk_name(name: str) -> bool:
    """Return True if the name indicates Hong Kong-listed underlying stocks."""
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
