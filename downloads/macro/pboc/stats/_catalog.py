"""_catalog.py — Definitions of the 5 PBoC statistical items to download.

Each item is identified by:
  * A ``slug`` used in output filenames (e.g. ``afre_flow``)
  * The source page category (``shrzgm`` = 社会融资规模, ``hbtjgl`` = 货币统计概览)
  * Chinese and English label patterns to match the row on the page
  * A human-readable description

The row matching uses regex against the full row text (Chinese + English
label concatenated), so partial matches work regardless of whitespace.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class StatsItem:
    """A target statistical item to download from PBoC."""
    slug: str                  # e.g. "afre_flow" — used in filenames
    page: str                  # "shrzgm" or "hbtjgl"
    cn_label: str              # e.g. "社会融资规模增量统计表"
    en_label: str              # e.g. "Aggregate Financing to the Real Economy (Flow)"
    match_patterns: List[str]  # regex patterns matched against row text
    description: str

    def matches(self, row_text: str) -> bool:
        """Return True if *row_text* matches any of this item's patterns."""
        for pat in self.match_patterns:
            if re.search(pat, row_text, re.IGNORECASE):
                return True
        return False


# ============================================================================
# The 5 target items requested by the user
# ============================================================================
TARGET_ITEMS: List[StatsItem] = [
    # --- shrzgm page (社会融资规模) ---
    StatsItem(
        slug="afre_flow",
        page="shrzgm",
        cn_label="社会融资规模增量统计表",
        en_label="Aggregate Financing to the Real Economy (Flow)",
        match_patterns=[
            r"社会融资规模增量",
            r"Aggregate Financing.*Flow",
            r"AFRE.*Flow",
        ],
        description="Aggregate Financing to the Real Economy (Flow/Increment)",
    ),
    StatsItem(
        slug="afre_stock",
        page="shrzgm",
        cn_label="社会融资规模存量统计表",
        en_label="Aggregate Financing to the Real Economy (Stock)",
        match_patterns=[
            r"社会融资规模存量",
            r"Aggregate Financing.*Stock",
            r"AFRE.*Stock",
        ],
        description="Aggregate Financing to the Real Economy (Stock)",
    ),
    # --- hbtjgl page (货币统计概览) ---
    StatsItem(
        slug="official_reserve",
        page="hbtjgl",
        cn_label="官方储备资产",
        en_label="Official reserve assets",
        match_patterns=[
            r"官方储备资产",
            r"Official reserve assets",
        ],
        description="Official Reserve Assets",
    ),
    StatsItem(
        slug="depository_corp_survey",
        page="hbtjgl",
        cn_label="存款性公司概览",
        en_label="Depository Corporations Survey",
        match_patterns=[
            r"存款性公司概览",
            r"Depository Corporations Survey",
        ],
        description="Depository Corporations Survey",
    ),
    StatsItem(
        slug="overseas_rmb_assets",
        page="hbtjgl",
        cn_label="境外机构和个人持有境内人民币金融资产情况",
        en_label="Domestic RMB Financial Assets Held by Overseas Entities",
        match_patterns=[
            r"境外机构.*人民币金融资产",
            r"Domestic RMB Financial Assets Held by Overseas",
        ],
        description="Domestic RMB Financial Assets Held by Overseas Entities",
    ),
]


# Items grouped by source page
SHRZGM_ITEMS: List[StatsItem] = [it for it in TARGET_ITEMS if it.page == "shrzgm"]
HBTJGL_ITEMS: List[StatsItem] = [it for it in TARGET_ITEMS if it.page == "hbtjgl"]


def items_for_page(page: str) -> List[StatsItem]:
    """Return target items belonging to the given page ('shrzgm' or 'hbtjgl')."""
    return [it for it in TARGET_ITEMS if it.page == page]


def find_matching_item(row_text: str, page: str) -> Optional[StatsItem]:
    """Find the first target item on *page* whose patterns match *row_text*."""
    for item in items_for_page(page):
        if item.matches(row_text):
            return item
    return None
