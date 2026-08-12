"""Stock name-pattern overrides — assign OTHER stocks to dummy index parents.

When a stock has NO qualifying industry index (weight > 2% threshold) AND
its name does not match any INDUSTRY_RULES keyword, classify_stocks falls
through to this module as a last-resort lookup.  Each entry maps a name
substring to a (sector_id, industry_id) pair — the stock inherits that
classification and gets a synthetic DUMMY_{industry_id} parent index so it
appears in the UI hierarchy alongside real-index-mapped stocks.

This module is SEPARATE from INDUSTRY_RULES because:
  1. Stock company names have different patterns than index/ETF names
     (e.g. "中原高速" the stock vs "中证高速公路指数" the index).
  2. Some patterns here DUPLICATE INDUSTRY_RULES keywords intentionally —
     they serve as a curated, documented fallback for stocks specifically.
  3. Some patterns are STOCK-SPECIFIC (e.g. "置业", "建工") and would cause
     false positives if added to INDUSTRY_RULES (which is also used for
     index/ETF classification).

Usage in classify_stocks (else branch):
  match = match_stock_override(stock_name)
  if match:
      sector_id, industry_id = match
      parent_index_code = dummy_code(industry_id)
"""
from __future__ import annotations

from typing import Optional, Tuple

from builds.classification.sector_industry.index.stock.dummy_ext import (
    dummy_code,
    ensure_dummy_index,
)


# Name-pattern → (sector_id, industry_id) mapping.
# Order matters: first match wins (top-to-bottom).  More specific patterns
# should come before broader ones.
STOCK_NAME_PATTERNS: list[tuple[str, str, str]] = [
    # === Patterns that DUPLICATE INDUSTRY_RULES keywords (for stocks that
    #     match keywords but have no real index parent — they get a DUMMY) ===

    # --- IND (工业) ---
    ("高速",     "IND",   "EXPRESSWAY"),     # 中原高速, 福建高速, 海南高速
    ("公路",     "IND",   "EXPRESSWAY"),     # 公路 operators
    ("电气",     "IND",   "ELEC_EQUIP"),     # 博菲电气, 智光电气
    ("重工",     "IND",   "ENG_MACHINERY"),  # 拓山重工, 大连重工
    ("港口",     "IND",   "PORT"),           # 招商港口
    ("机场",     "IND",   "AIRPORT"),        # 深圳机场, 白云机场
    ("纺织",     "IND",   "TEXTILE"),        # 深纺织, 新野纺织
    ("服饰",     "IND",   "TEXTILE"),        # 美邦服饰, 森马服饰
    ("服装",     "IND",   "TEXTILE"),        # 星星服装
    ("纸业",     "IND",   "PAPER"),          # 晨鸣纸业, 太阳纸业
    ("印刷",     "IND",   "PRINTING"),       # 印刷 companies

    # --- INFRA (基建) ---
    ("水务",     "INFRA", "WATER"),          # 重庆水务, 江南水务
    ("燃气",     "INFRA", "GAS"),            # 贵州燃气, 长春燃气
    ("建工",     "INFRA", "INFRA_CONSTR"),   # 上海建工, 建工修复
    ("建设",     "INFRA", "INFRA_CONSTR"),   # 中南建设, 宏润建设

    # --- CONS (消费) ---
    ("百货",     "CONS",  "RETAIL"),         # 合肥百货, 南宁百货
    ("超市",     "CONS",  "RETAIL"),         # 永辉超市
    ("商业",     "CONS",  "RETAIL"),         # 中兴商业, 茂业商业
    ("酒店",     "CONS",  "HOTEL"),          # 金陵饭店
    ("饭店",     "CONS",  "HOTEL"),          # 饭店
    ("文旅",     "CONS",  "TOURISM"),        # 祥源文旅, 曲江文旅

    # --- HC (医药) ---
    ("药业",     "HC",    "PHARMA_BROAD"),   # 丰原药业, 启迪药业, 仁和药业
    ("医院",     "HC",    "HEALTH"),         # 莲池医院

    # --- RE (地产) ---
    ("置业",     "RE",    "RE_REAL_ESTATE"), # 美好置业, 南国置业, 京能置业

    # --- ENG (能源) ---
    ("矿业",     "ENG",   "ENG_GENERAL"),    # 金岭矿业, 西藏矿业

    # --- FIN (金融) ---
    ("期货",     "FIN",   "FIN_GENERAL"),    # 弘业期货, 瑞达期货, 永安期货
    ("信托",     "FIN",   "FIN_GENERAL"),    # 建元信托

    # --- AERO (航空航天) ---
    ("航空",     "AERO",  "AERO_AVIATION"),  # 航空 companies (not 航天)
]


def match_stock_override(
    stock_name: str,
) -> Optional[Tuple[str, str, str]]:
    """Look up a stock name against STOCK_NAME_PATTERNS.

    Returns (sector_id, industry_id, dummy_parent_code) if a pattern matches,
    or None if no pattern matches (stock stays at OTHER).

    The first matching pattern wins (patterns are checked top-to-bottom).
    """
    if not stock_name:
        return None
    for pattern, sector_id, industry_id in STOCK_NAME_PATTERNS:
        if pattern in stock_name:
            return (sector_id, industry_id, dummy_code(industry_id))
    return None


__all__ = [
    "STOCK_NAME_PATTERNS",
    "match_stock_override",
    "dummy_code",
    "ensure_dummy_index",
]
