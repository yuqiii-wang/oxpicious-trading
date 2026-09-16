"""llm_agents.online_search_summary.storage.matching — Ref typing vocabulary + match helpers.

The text.llm_qa_refs vocabulary and the pure text/URL/time helpers shared
by the resolution steps (storage.resolve) and the persistence layer
(storage.store). Nothing here touches the network or the DB.

ref_type — the typing is PROVENANCE (where the article came from, not how
strongly it was verified):

  * ``exact``    — the article is the one the provider's summary response
                   returned: rows stored straight from the response echo
                   (title/link/date metadata, verified or not) and echo
                   links that were fetched and content-confirmed
                   (resolved_via 'summary').
  * ``relevant`` — the article was found AFTER the summary by title
                   search from other sources: a stored-corpus title match
                   (resolved_via 'corpus') or a ddgs-discovered candidate
                   page (resolved_via 'ddgs').
"""
from __future__ import annotations

import datetime
import re
from typing import Optional
from urllib.parse import urlparse

from llm_agents.online_search_summary.core.models import (
    SHANGHAI_TZ, SearchHit,
)

REF_TYPE_EXACT = "exact"
REF_TYPE_RELEVANT = "relevant"

# Provenance stored in text.llm_qa_refs.resolved_via — where the resolved
# article came from (drives the feed's ref tags).
VIA_SUMMARY = "summary"   # came with the provider's summary response
VIA_CORPUS = "corpus"     # matched afterwards by title in the stored corpus
VIA_DDGS = "ddgs"         # found afterwards by ddgs title search + fetch
VIA_ZHIHU = "zhihu"       # found by zhihu online search (zhihu provider)


def normalize_text(s: Optional[str]) -> str:
    """Whitespace-stripped, lowercased text for containment checks."""
    return re.sub(r"\s+", "", s or "").lower()


def is_exact_article(title: str, snippet: Optional[str],
                     page_text: str) -> bool:
    """True when *page_text* (markitdown-extracted) contains the article.

    Two accepted proofs: the normalized ref title appears in the page, or
    a long-enough prefix of the normalized snippet does (search snippets
    are verbatim page text, and headlines are sometimes re-worded by the
    page's own <title>/heading).
    """
    page = normalize_text(page_text)
    if not page:
        return False
    t = normalize_text(title)
    if t and t in page:
        return True
    s = normalize_text(snippet)
    if len(s) >= 30 and s[:60] in page:
        return True
    return False


def source_from_url(url: Optional[str]) -> Optional[str]:
    """Site domain as the text.news source for web-fetched articles."""
    if not url:
        return None
    netloc = urlparse(url).netloc.lower()
    if not netloc:
        return None
    return netloc[4:] if netloc.startswith("www.") else netloc


def hit_ref_time(hit: SearchHit) -> Optional[datetime.datetime]:
    """The reference's publish time per the search response.

    publish_date (a date) is anchored at midnight Asia/Shanghai; hits the
    response carried no date for yield None — the caller defaults those to
    now() when writing text.llm_qa_refs.ref_time.
    """
    if hit.publish_date is None:
        return None
    return datetime.datetime.combine(
        hit.publish_date, datetime.time.min, tzinfo=SHANGHAI_TZ)
