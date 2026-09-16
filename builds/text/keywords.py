"""builds.text.keywords — Industry tags + keyword extraction for news text.

Integrates two sources into one keyword taxonomy (the "SEC classification
study"):

  1. ``_common/sec_statics/sec_classification.json`` — the project's
     CANONICAL sector/industry catalog (sector_id, industry_id, label, slug).
     Canonical industry ids are UPPERCASE slugs (BANKS, SEMI, …) — the same
     values stored in stats.sec_classification / stats.industry_basic_stats,
     so ``text.news.industry_id`` / ``text.news_keywords.industry_id`` join
     against the rest of the project without mapping. The matched industry's
     parent sector is carried alongside as ``text.news.sector_id``.

  2. ``downloads/macro/gov/keywords.json`` — the curated NEWS keyword
     taxonomy. Its industry category names are the catalog industry SLUGS
     (lowercase: "banks", "semi", …), so each keyword list attaches to its
     canonical industry_id via the catalog slug. The 'broadmarket' type holds
     macro-theme categories (economy, monetary, …) that have NO catalog
     industry — their keywords are extracted as keywords but never set
     ``text.news.industry_id``.

  Integration rule: for every catalog industry the keyword set is
  {catalog label} ∪ {keywords.json keywords for that slug}. The catalog label
  (e.g. 银行 for BANKS) is a natural news keyword and keeps articles that use
  only the label taggable.

Extraction: substring counting of every taxonomy keyword over title+content.
Pure-ASCII keywords (AI, 5G, VR, EDA, PC, …) match whole-word only and
case-insensitively, so "AI" does not hit inside English words; Chinese
keywords match as plain substrings. ``word_count`` = CJK chars + ASCII word
tokens (the denominator of ``text.news_keywords.count_pct``).

Industry assignment: when an article matches several industries, the winner
is the one appearing FIRST in the catalog (sector rule order — same
tie-breaking convention as _common.sec_statics.classification).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from builds.text import paths

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Taxonomy construction
# ----------------------------------------------------------------------------
def _load_catalog() -> Dict[str, Any]:
    with open(paths.SEC_CLASSIFICATION_JSON, encoding="utf-8") as f:
        return json.load(f)["catalog"]


def _load_news_keywords_json() -> Dict[str, Any]:
    with open(paths.GOV_KEYWORDS_JSON, encoding="utf-8") as f:
        return json.load(f)


class KeywordTaxonomy:
    """Integrated keyword → industry map + compiled matchers."""

    def __init__(self) -> None:
        catalog = _load_catalog()
        news_kw = _load_news_keywords_json()

        # slug (lowercase) -> industry_id (UPPER), and industry_id -> label,
        # both in catalog rule order (dict order = rule order).
        self.slug_to_industry: Dict[str, str] = {}
        self.industry_label: Dict[str, str] = {}
        self.industry_sector: Dict[str, str] = {}
        self.industry_order: Dict[str, int] = {}
        order = 0
        for _sector_id, sector in catalog.items():
            for industry_id, info in sector.get("industries", {}).items():
                if industry_id == "OTHER":  # fallback bucket — not taggable
                    continue
                slug = info.get("slug") or industry_id.lower()
                self.slug_to_industry[slug] = industry_id
                self.industry_label[industry_id] = info.get("label", "")
                self.industry_sector[industry_id] = _sector_id
                self.industry_order[industry_id] = order
                order += 1

        # keyword -> set of industry_ids; broadmarket keywords -> no industry.
        kw_to_industries: Dict[str, set] = {}
        unmatched_slugs: set = set()
        for type_name, categories in news_kw.items():
            if type_name == "_comment":
                continue
            for category, keywords in categories.items():
                industry_id = self.slug_to_industry.get(category)
                if type_name != "broadmarket" and industry_id is None:
                    # Industry-typed category with no catalog match would
                    # silently drop its tag — surface it once at build time.
                    unmatched_slugs.add(f"{type_name}.{category}")
                    continue
                for kw in keywords:
                    kw = kw.strip()
                    if not kw:
                        continue
                    if industry_id is None:
                        # broadmarket (macro-theme) keyword — extracted as a
                        # keyword only, never sets text.news.industry_id.
                        kw_to_industries.setdefault(kw, set())
                    else:
                        kw_to_industries.setdefault(kw, set()).add(industry_id)

        # Catalog labels join their industry's keyword set (see module
        # docstring); labels never map to an industry they don't belong to.
        for industry_id, label in self.industry_label.items():
            if label:
                kw_to_industries.setdefault(label, set()).add(industry_id)

        if unmatched_slugs:
            logger.warning(
                "    [keywords] news-keyword categories without a "
                "sec_classification match (not taggable as industry): %s",
                sorted(unmatched_slugs))

        self.kw_to_industries: Dict[str, Optional[set]] = {
            kw: industries or None for kw, industries in kw_to_industries.items()}
        # Match order: longer keywords first so specific terms are counted
        # before (and independently of) their substrings; ties alphabetical.
        self._matchers: List[Tuple[str, re.Pattern, bool]] = [
            (kw, self._compile(kw), bool(re.fullmatch(r"[A-Za-z0-9]+", kw)))
            for kw in sorted(self.kw_to_industries, key=lambda k: (-len(k), k))
        ]

    @staticmethod
    def _compile(keyword: str) -> re.Pattern:
        if re.fullmatch(r"[A-Za-z0-9]+", keyword):
            return re.compile(
                r"(?<![A-Za-z0-9])" + re.escape(keyword) + r"(?![A-Za-z0-9])",
                re.IGNORECASE)
        return re.compile(re.escape(keyword))

    @staticmethod
    def word_count(text: str) -> int:
        """CJK chars + ASCII word tokens (count_pct denominator)."""
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        return cjk + len(re.findall(r"[A-Za-z0-9]+", text))

    def match(self, text: str) -> Tuple[Dict[str, int], Optional[str]]:
        """Extract keyword counts from *text*.

        Returns (keyword -> count, primary_industry_id | None). The primary
        industry is the first catalog-ordered industry among matched industry
        keywords (None when only broadmarket keywords matched, or no match).
        """
        counts: Dict[str, int] = {}
        matched_industries: set = set()
        for kw, pattern, is_ascii in self._matchers:
            if not is_ascii and kw not in text:
                # Cheap prefilter for Chinese keywords. ASCII keywords are
                # matched case-insensitively by the regex, so they can't use
                # this case-sensitive `in` check.
                continue
            n = len(pattern.findall(text))
            if n:
                counts[kw] = n
                industries = self.kw_to_industries[kw]
                if industries:
                    matched_industries |= industries
        industry_id = None
        if matched_industries:
            industry_id = min(matched_industries, key=self.industry_order.get)
        return counts, industry_id

    def sector_of(self, industry_id: Optional[str]) -> Optional[str]:
        """Parent sector_id of *industry_id* in the catalog (None when the
        industry is unknown or *industry_id* is None)."""
        if industry_id is None:
            return None
        return self.industry_sector.get(industry_id)


# Process-wide singleton: the taxonomy is read-only after construction,
# but construction re-reads two JSON catalogs and rebuilds the compiled
# matchers — callers that run per-article/per-QA (builds.text loaders,
# llm_agents stores) share one instance instead of paying that per call.
_TAXONOMY: Optional["KeywordTaxonomy"] = None


def get_taxonomy() -> "KeywordTaxonomy":
    """The shared KeywordTaxonomy instance (built on first use)."""
    global _TAXONOMY
    if _TAXONOMY is None:
        _TAXONOMY = KeywordTaxonomy()
    return _TAXONOMY


def extract_for_articles(
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Attach industry_id / sector_id / word_count / keywords to news rows.

    Adds in place + returns the same rows, each with:
      industry_id TEXT | None, sector_id TEXT | None (its parent sector),
      word_count INT, keywords: dict keyword -> count (matched keywords only).
    """
    taxonomy = get_taxonomy()
    for row in rows:
        text = row["title"] + "\n" + (row.get("content") or "")
        counts, industry_id = taxonomy.match(text)
        row["industry_id"] = industry_id
        row["sector_id"] = taxonomy.sector_of(industry_id)
        row["word_count"] = taxonomy.word_count(text)
        row["keywords"] = counts
    return rows
