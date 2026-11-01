"""_common.keyword_maps — reader for the static.keyword_maps seed table.

static.keyword_maps (database/sql/static/keyword_maps/) is the seeded
keyword → category map for news-text auditing: industry rows (keyword →
canonical sec_classification industry_id), market-description rows (下跌 →
px_dn, 放量 → vol_up, …), broadmarket macro-theme rows, and skip rows (the
low-semantic function-word vocabulary — 其实/也/就是说, the/a/of — the
search-index tokenization drops from every token stream; never extracted
as keywords),
each with its lexical class (parts_of_speech) and negation flag
(is_negation — a preceding 没有/并未/未/不/… flips the keyword; the
extraction drops negated occurrences of flagged keywords). It replaced the
former ``downloads/macro/gov/keywords.json`` file taxonomy (2026-11-01) —
edit the seed files to change the vocabulary, never this module.

No caching here: callers that run per-request/per-article should hold one
result (see builds.text.keywords.get_taxonomy's process-wide singleton and
_common.tokenization.search_indexing's cached skip-word set).
"""
from __future__ import annotations

from typing import List, NamedTuple, Optional

from _common.db_commons import get_read_db_connection
from _common.labels import KW_CATEGORY_TYPE_CODE


class KeywordMapRow(NamedTuple):
    """One row of static.keyword_maps.

    category_type is the static.label_dim 'kw_category_type' code
    (0 industry | 1 market | 2 broadmarket | 3 skip); the target columns
    follow the table's selection rule — industry_id set iff type=industry,
    category_code ('kw_market' / 'kw_theme' code) iff type=market or
    broadmarket, both NULL for skip rows. parts_of_speech is the 'kw_pos'
    code (0 noun | 1 verb | 2 noun_verb | 3 function); is_negation marks
    the keywords whose meaning a preceding negator flips.
    """

    keyword: str
    category_type: int
    industry_id: Optional[str]
    category_code: Optional[int]
    parts_of_speech: int
    is_negation: bool
    language: int


def load_keyword_maps() -> List[KeywordMapRow]:
    """All rows of static.keyword_maps (read replica; small seed table)."""
    conn = get_read_db_connection()
    try:
        return [
            KeywordMapRow(*row) for row in conn.execute(
                "SELECT keyword, category_type, industry_id, category_code, "
                "parts_of_speech, is_negation, language "
                "FROM static.keyword_maps "
                "ORDER BY keyword, category_type").fetchall()
        ]
    finally:
        conn.close()


def load_skip_words() -> List[str]:
    """The skip vocabulary (category_type=skip): the low-semantic function
    words the search-index tokenization drops from every token stream.

    Read per call — small; the tokenization layer holds the process-wide
    set (search_indexing._skip_words).
    """
    conn = get_read_db_connection()
    try:
        return [
            row[0] for row in conn.execute(
                "SELECT keyword FROM static.keyword_maps "
                "WHERE category_type = %s ORDER BY keyword",
                (KW_CATEGORY_TYPE_CODE["skip"],)).fetchall()
        ]
    finally:
        conn.close()
