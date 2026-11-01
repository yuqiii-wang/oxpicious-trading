"""Token-stream → keyword_map keyword matching via up-to-4-gram joining.

The search-index token stream splits many seeded keywords into pieces:
大幅上涨 segments to 大幅/上涨, 涨停板 to 涨停/板, "limit up" to
limit/up — so a bare token can only ever be a SUBSET of such a keyword,
and exact token→keyword equality matches single-token keywords only.
This module closes the gap: consecutive tokens are joined back into
n-grams (n = 1..NGRAM_MAX) and each n-gram is matched against the
static.keyword_maps vocabulary of every category_type except skip — the
extraction families (industry / market / broadmarket) plus the
grammatical component families (adj / adv / adv_noun / adv_verb —
大幅/高位/持续, the modifier layer that composes with the market verbs,
e.g. the query "高位持续大幅上涨" matches 高位, 持续, 大幅, 上涨 AND the
seeded compound 大幅上涨); skip rows are dropped from the stream upstream
and never matchable:

    match_keywords(["大幅", "上涨", "银行"]) -> {"大幅上涨": 1, "上涨": 1, "银行": 1}

Join rule per n-gram: an all-ASCII token run space-joins LOWERCASED
(``limit`` + ``up`` -> "limit up"; en keywords are stored lowercased with
hyphens normalized to spaces, so range/bound matches the seeded
"range-bound" — the same bridging builds.text.keywords compiles into its
regex); any CJK token makes the n-gram a direct concatenation of the
original tokens (大幅 + 上涨 -> "大幅上涨", the zh seed form).

Matching runs on the POST-skip token stream (the same words the tsvector
indexes — callers pass e.g. a token string from
:meth:`SearchIndexer.tokenize_for_search_batch` split back apart).  An
n-gram may therefore span a dropped skip word, joining tokens that were
filler-separated in the raw text — accepted: the fillers are exactly the
low-semantic words this pipeline drops.

Counts follow the extraction's overlap convention (builds.text.keywords):
every n-gram at every position counts independently — 上涨 also counts
inside each 大幅上涨 occurrence, and the dict is ordered by first
occurrence (Python dict order).

The vocabulary caches once per process; a failed load degrades to an
EMPTY vocabulary (log once) — matching must never break on the DB being
unreachable, mirroring search_indexing._skip_words.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Tuple

logger = logging.getLogger(__name__)

# Longest keyword span the n-gram joining reconstructs (user-directed
# ceiling; the study temp_scripts/study_keyword_ngrams.py measures the
# seeded vocabulary's real token-span distribution under the segmenter).
NGRAM_MAX = 4

# The taxonomy's english-like keyword shape (builds.text.keywords
# _ENGLISH_LIKE_RE — replicated here; _common must not import builds):
# ASCII tokens/words, optional spaces and hyphens. Everything else (any
# CJK or other non-ASCII char) matches as a zh keyword.
_ENGLISH_LIKE_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9 '\-]*[A-Za-z0-9]|[A-Za-z0-9]")

# (zh keywords verbatim, en keywords lowercased + hyphens -> spaces)
_VOCAB: "Tuple[frozenset[str], frozenset[str]] | None" = None


def _vocab() -> "Tuple[frozenset[str], frozenset[str]]":
    """The matchable extraction vocabulary, cached once per process.

    (zh keywords, lowercase en keywords) from static.keyword_maps — every
    category_type except skip (skip rows are the tokenization's dropped
    stop vocabulary, never matchable keywords).  Classification is by
    CONTENT (pure-ASCII = en-like), not the language column, mirroring how
    the taxonomy compiles its matchers; en keywords normalize case and
    hyphens so token joins compare equal to the seeded form.
    """
    global _VOCAB
    if _VOCAB is None:
        try:
            from _common.keyword_maps import load_keyword_maps
            from _common.labels import KW_CATEGORY_TYPE_CODE

            skip = KW_CATEGORY_TYPE_CODE["skip"]
            zh: set[str] = set()
            en: set[str] = set()
            for row in load_keyword_maps():
                if row.category_type == skip:
                    continue
                kw = row.keyword.strip()
                if not kw:
                    continue
                if _ENGLISH_LIKE_RE.fullmatch(kw):
                    en.add(kw.lower().replace("-", " "))
                else:
                    zh.add(kw)
            _VOCAB = (frozenset(zh), frozenset(en))
            logger.debug(
                "keyword ngrams: vocab loaded (%d zh, %d en)",
                len(zh), len(en))
        except Exception as exc:  # noqa: BLE001 — degrade, never break
            logger.warning(
                "keyword ngrams: vocab load failed, nothing matches (%r)",
                exc)
            _VOCAB = (frozenset(), frozenset())
    return _VOCAB


def match_keywords(words: Sequence[str]) -> dict[str, int]:
    """Match taxonomy keywords in a token stream via 1..NGRAM_MAX-grams.

    *words* is one text's token stream (post-skip — e.g. a token string
    from :meth:`SearchIndexer.tokenize_for_search_batch` split on
    spaces).  Returns keyword -> occurrence count, ordered by first
    occurrence; overlapping n-grams each count (上涨 counts inside
    大幅上涨 too).  Empty stream / empty vocabulary -> {}.
    """
    zh_vocab, en_vocab = _vocab()
    if not words or not (zh_vocab or en_vocab):
        return {}
    n = len(words)
    lowered = [w.lower() for w in words]
    counts: dict[str, int] = {}
    for i in range(n):
        for span in range(1, min(NGRAM_MAX, n - i) + 1):
            j = i + span
            gram = lowered[i:j]
            if all(t.isascii() for t in gram):
                # Hyphens normalize to spaces on BOTH sides: the segmenter
                # keeps hyphenated words whole (e-commerce stays one token)
                # while the seeded keyword "e-commerce" normalizes to
                # "e commerce" — the same bridging the taxonomy's regex does.
                cand = " ".join(gram).replace("-", " ")
                if cand in en_vocab:
                    counts[cand] = counts.get(cand, 0) + 1
            else:
                cand = "".join(words[i:j])
                if cand in zh_vocab:
                    counts[cand] = counts.get(cand, 0) + 1
    return counts


def match_keywords_batch(
    words_per_text: "Sequence[Sequence[str]]",
) -> "list[dict[str, int]]":
    """Batch of :func:`match_keywords` — one token stream per text."""
    return [match_keywords(words) for words in words_per_text]
