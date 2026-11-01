"""builds.text.keywords — Industry tags + keyword extraction for news text.

Integrates two sources into one keyword taxonomy (the "SEC classification
study"):

  1. ``static.keyword_maps`` (DB seed, database/sql/static/keyword_maps/ —
     read via :func:`_common.keyword_maps.load_keyword_maps`) — the seeded
     keyword → category map, source of record for the vocabulary. Four
     category types: industry rows map a keyword to the canonical
     sec_classification industry_id (UPPERCASE: POWER_GRID, BANKS, … — the
     same values stored in stats.sec_classification /
     stats.industry_basic_stats, so ``text.news.industry_id`` /
     ``text.news_keywords.industry_id`` join against the rest of the
     project without mapping; every catalog industry's own label is seeded
     as its industry's keyword too); market-description rows (下跌 → px_dn,
     放量 → vol_up, …) and broadmarket macro-theme rows (monetary, fiscal,
     …) are extracted as keywords but never set ``text.news.industry_id``;
     skip rows (其实/也/就是说-class low-semantic function words) are the
     search-index tokenization's stop vocabulary and the component rows
     (adj / adv / adv_noun / adv_verb — 大幅/高位/持续, the modifier layer
     the keyword n-gram matcher composes with the market verbs) are
     grammar — this taxonomy EXCLUDES both (they are never
     matchable/extractable audit keywords); the tokenization filter lives
     in _common.tokenization.search_indexing.

  2. ``database/sec_statics/sec_classification/`` (shard tree, read via
     ``classification_store.load_catalog``) — the project's CANONICAL
     sector/industry catalog, used for industry METADATA only now: the
     matched industry's parent sector (``text.news.sector_id``) and the
     catalog rule order that breaks multi-industry-match ties. The matched
     industry's label no longer joins the keyword set here — it is seeded
     in the table (02_seed_industry.sql); a label missing from the seed
     surfaces once at build time as a drift warning.

Extraction: substring counting of every taxonomy keyword over title+content.
English keywords (language=en — pure-ASCII tokens and multi-word phrases)
match whole-word and case-insensitively, so "AI" does not hit inside
English words and "limit up" also hits "limit-up"; Chinese keywords match
as plain substrings. Negated occurrences of negation-
sensitive keywords (seeded ``is_negation`` — a preceding 没有/并未/尚未/
不再/没/不/未/无/非 or English no/not/never/without flips the reading:
并未下跌 is NOT down-talk) don't
count. ``word_count`` = CJK chars + ASCII word tokens (the denominator of
``text.news_keywords.count_pct``).

Industry assignment: when an article matches several industries, the winner
is the one appearing FIRST in the catalog (sector rule order — same
tie-breaking convention as database.sec_statics.classification). ONE
exception, applied only in :func:`extract_for_articles` (the text.news
tagging path — llm_agents QA tagging keeps the plain rule): a general daily
broad-market recap item (zhihu 「如何看待…A股市场行情?」 answers, media
A股收评/午评/开盘 pieces, the ai_daily 整体行情 question) is ALWAYS tagged
上证 (BROAD_SSE) — see :data:`BROAD_MARKET_RECAP_INDUSTRY_ID`.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from _common.keyword_maps import load_keyword_maps
from _common.labels import KW_CATEGORY_TYPE_CODE
from database.sec_statics import classification_store

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Taxonomy construction
# ----------------------------------------------------------------------------
def _load_catalog() -> Dict[str, Any]:
    return classification_store.load_catalog()


class KeywordTaxonomy:
    """Integrated keyword → industry map + compiled matchers."""

    def __init__(self) -> None:
        catalog = _load_catalog()
        keyword_rows = load_keyword_maps()

        # Catalog metadata in rule order (dict order = rule order): label /
        # parent sector / tie-break order per industry_id. The rows carry
        # industry_id directly, so the catalog no longer resolves slugs.
        self.industry_label: Dict[str, str] = {}
        self.industry_sector: Dict[str, str] = {}
        self.industry_order: Dict[str, int] = {}
        order = 0
        for _sector_id, sector in catalog.items():
            for industry_id, info in sector.get("industries", {}).items():
                if industry_id == "OTHER":  # fallback bucket — not taggable
                    continue
                self.industry_label[industry_id] = info.get("label", "")
                self.industry_sector[industry_id] = _sector_id
                self.industry_order[industry_id] = order
                order += 1

        # keyword -> set of industry_ids; market / broadmarket keywords ->
        # no industry. setdefault for non-industry rows so an industry row
        # the same keyword also has survives (the former JSON union rule).
        # Extraction is an ALLOWLIST on the three audit families: skip
        # rows (the tokenization's stop vocabulary) and the grammatical
        # component rows (adj / adv / adv_noun / adv_verb — 大幅/高位/持续,
        # the modifier layer the keyword n-gram matcher composes with the
        # market verbs) are never matchable/extractable — a bare 大幅 or
        # 持续 mention evidences no market state, no theme, no industry —
        # and any future non-audit type lands excluded automatically.
        extraction_types = (
            KW_CATEGORY_TYPE_CODE["industry"],
            KW_CATEGORY_TYPE_CODE["market"],
            KW_CATEGORY_TYPE_CODE["broadmarket"],
        )
        type_industry = KW_CATEGORY_TYPE_CODE["industry"]
        kw_to_industries: Dict[str, set] = {}
        unknown_industries: set = set()
        negation_sensitive: set = set()
        for row in keyword_rows:
            kw = row.keyword.strip()
            if not kw:
                continue
            if row.category_type not in extraction_types:
                continue
            if row.is_negation:
                negation_sensitive.add(kw)
            if row.category_type == type_industry:
                if row.industry_id not in self.industry_order:
                    # A seed row naming an industry the catalog dropped
                    # would silently stop tagging — surface it once.
                    unknown_industries.add(row.industry_id)
                    continue
                kw_to_industries.setdefault(kw, set()).add(row.industry_id)
            else:
                kw_to_industries.setdefault(kw, set())
        if unknown_industries:
            logger.warning(
                "    [keywords] keyword_maps industry rows with no "
                "sec_classification match (re-seed 02_seed_industry.sql): %s",
                sorted(unknown_industries))

        # Drift guard: a catalog label missing from the seeded table would
        # silently stop tagging articles that use only the label.
        unseeded_labels = sorted(
            label for industry_id, label in self.industry_label.items()
            if label and label not in kw_to_industries)
        if unseeded_labels:
            logger.warning(
                "    [keywords] catalog labels not seeded in "
                "static.keyword_maps (articles naming only them stop "
                "tagging): %s", unseeded_labels)

        self.kw_to_industries: Dict[str, Optional[set]] = {
            kw: industries or None for kw, industries in kw_to_industries.items()}
        # Keywords whose meaning a preceding negator flips (seeded
        # is_negation): their negated occurrences don't count (see match).
        self.kw_negation_sensitive: set = negation_sensitive
        # Match order: longer keywords first so specific terms are counted
        # before (and independently of) their substrings; ties alphabetical.
        # The bool rides in the tuple as "english-like" — those match
        # case-insensitively and must not use the cheap case-sensitive
        # prefilter below.
        self._matchers: List[Tuple[str, re.Pattern, bool]] = [
            (kw, self._compile(kw),
             bool(self._ENGLISH_LIKE_RE.fullmatch(kw)))
            for kw in sorted(self.kw_to_industries, key=lambda k: (-len(k), k))
        ]

    # English-like keywords (ASCII tokens/words, optional spaces and
    # hyphens): matched whole-word and case-insensitive; multi-word forms
    # bridge whitespace/hyphen so "limit up" also hits "limit-up". These
    # skip the cheap case-sensitive `in` prefilter (case differs).
    _ENGLISH_LIKE_RE = re.compile(
        r"[A-Za-z0-9][A-Za-z0-9 '\-]*[A-Za-z0-9]|[A-Za-z0-9]")

    @classmethod
    def _compile(cls, keyword: str) -> re.Pattern:
        if cls._ENGLISH_LIKE_RE.fullmatch(keyword):
            parts = keyword.split()
            body = r"[\s\-]+".join(re.escape(p) for p in parts)
            return re.compile(
                r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])",
                re.IGNORECASE)
        return re.compile(re.escape(keyword))

    @staticmethod
    def word_count(text: str) -> int:
        """CJK chars + ASCII word tokens (count_pct denominator)."""
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        return cjk + len(re.findall(r"[A-Za-z0-9]+", text))

    # Negation tokens that flip a negation-sensitive keyword when they
    # IMMEDIATELY precede the match (the user's spec: the previous word).
    # Multi-char forms first, then the single-char family; the immediate-
    # suffix check is naturally safe against lookalikes because the token
    # must END right at the keyword (非常利好 → "常" intervenes; 未来上涨 →
    # "来" intervenes; 不断下跌 → "断" intervenes — none match). English
    # negators are checked as the trailing word before the match.
    _NEGATION_WORDS = (
        "没有", "并非", "并未", "并不", "未曾", "尚未", "未能", "不再",
        "毫无", "而非", "没", "不", "未", "无", "非",
    )
    _EN_NEGATION_WORDS = frozenset(
        {"no", "not", "never", "without", "hardly", "barely"})

    @classmethod
    def _negated_before(cls, text: str, start: int) -> bool:
        """True when a negation token immediately precedes *start*."""
        if (text[max(0, start - 2):start] in cls._NEGATION_WORDS
                or text[max(0, start - 1):start] in cls._NEGATION_WORDS):
            return True
        m = re.search(r"[A-Za-z]+\s*$", text[:start])
        return bool(m and m.group(0).strip().lower()
                    in cls._EN_NEGATION_WORDS)

    def match(self, text: str) -> Tuple[Dict[str, int], Optional[str]]:
        """Extract keyword counts from *text*.

        Returns (keyword -> count, primary_industry_id | None). The primary
        industry is the first catalog-ordered industry among matched industry
        keywords (None when only broadmarket keywords matched, or no match).
        Negated occurrences of negation-sensitive keywords (seeded
        is_negation — 并未下跌, 没有放量, 并非利好) don't count: the audit
        reads them as the OPPOSITE state, not as the keyword's category.
        """
        counts: Dict[str, int] = {}
        matched_industries: set = set()
        for kw, pattern, is_english in self._matchers:
            if not is_english and kw not in text:
                # Cheap prefilter for non-English keywords. English-like
                # keywords are matched case-insensitively by the regex, so
                # they can't use this case-sensitive `in` check.
                continue
            if kw in self.kw_negation_sensitive:
                n = sum(1 for m in pattern.finditer(text)
                        if not self._negated_before(text, m.start()))
            else:
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
# but construction re-reads the catalog JSON and the static.keyword_maps
# seed table — callers that run per-article/per-QA (builds.text loaders,
# llm_agents stores) share one instance instead of paying that per call.
_TAXONOMY: Optional["KeywordTaxonomy"] = None


def get_taxonomy() -> "KeywordTaxonomy":
    """The shared KeywordTaxonomy instance (built on first use)."""
    global _TAXONOMY
    if _TAXONOMY is None:
        _TAXONOMY = KeywordTaxonomy()
    return _TAXONOMY


# ----------------------------------------------------------------------------
# General daily broad-market recaps → 上证 (BROAD_SSE)
# ----------------------------------------------------------------------------
# A recap item IS a broad-market view, but its body name-drops sectors and 15
# of the 21 catalog sectors precede BROAD in rule order, so the earliest-match
# rule almost never picks a BROAD industry for it. Title shape decides: a
# broad-market term AND a market-session term in the TITLE (calibrated
# 2026-10-03 against all 25 866 distinct text.news titles — every match is a
# market recap/analysis/commentary piece; 查询 excludes the single-stock
# quote-lookup posts 「…股票行情…走势查询」).
BROAD_MARKET_RECAP_INDUSTRY_ID = "BROAD_SSE"
_BROAD_RECAP_TERM = re.compile(r"A\s*股|股市|大盘|沪指|上证指数|股票市场")
_BROAD_RECAP_SESSION_TERM = re.compile(r"行情|市场|走势|收盘|收评|午评|开盘|复盘")
_BROAD_RECAP_QUOTE_LOOKUP = re.compile(r"查询")


def is_general_broad_recap_title(title: str) -> bool:
    """True when *title* names a general daily broad-market recap item."""
    return bool(_BROAD_RECAP_TERM.search(title)
                and _BROAD_RECAP_SESSION_TERM.search(title)
                and not _BROAD_RECAP_QUOTE_LOOKUP.search(title))


def extract_for_articles(
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Attach industry_id / sector_id / word_count / keywords to news rows.

    Adds in place + returns the same rows, each with:
      industry_id TEXT | None, sector_id TEXT | None (its parent sector),
      word_count INT, keywords: dict keyword -> count (matched keywords only).

    A row that already carries an industry_id keeps it (ai_daily movers
    rows are pre-set with the movers plan's own industry — stronger than a
    text match); its sector_id is still resolved from the taxonomy.

    A general daily broad-market recap title (see
    :func:`is_general_broad_recap_title`) overrides the text-match primary
    with 上证 (BROAD_SSE) — before the pre-set check, so a loader-preset
    industry still wins over everything.
    """
    taxonomy = get_taxonomy()
    for row in rows:
        title = row["title"]
        text = title + "\n" + (row.get("content") or "")
        counts, industry_id = taxonomy.match(text)
        if is_general_broad_recap_title(title):
            industry_id = BROAD_MARKET_RECAP_INDUSTRY_ID
        row["industry_id"] = row.get("industry_id") or industry_id
        row["sector_id"] = taxonomy.sector_of(row["industry_id"])
        row["word_count"] = taxonomy.word_count(text)
        row["keywords"] = counts
    return rows


def extract_for_qa(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach keyword occurrences to text.llm_qa rows (the QA-side audit).

    Same taxonomy, counting, and negation rules as the news audit
    (:meth:`KeywordTaxonomy.match` — 并未下跌 does not count): one pass
    over each post's question + answer (the corpus the feed shows; the
    never-rendered ``context`` is NOT audited). Adds ``keywords``:
    dict keyword -> count, in place. No industry inference — QA rows carry
    their own ``industry_id`` (the QA flow's tagging), unlike
    :func:`extract_for_articles`.
    """
    taxonomy = get_taxonomy()
    for row in rows:
        text = row["question"] + "\n" + (row.get("answer") or "")
        counts, _ = taxonomy.match(text)
        row["keywords"] = counts
    return rows
