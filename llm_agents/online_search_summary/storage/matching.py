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
    """The reference's ACTUAL publish time, or None when unknown.

    The search response's publish_date (anchored at midnight
    Asia/Shanghai), else the date embedded in the article URL. None is
    stored as NULL — never defaulted to now()/the qa date, which wrote the
    row-creation date as the article's date.
    """
    d = hit.publish_date or date_from_url(hit.link)
    if d is None:
        return None
    return datetime.datetime.combine(d, datetime.time.min,
                                     tzinfo=SHANGHAI_TZ)


# A stored ref's known date must sit within [qa interest day - 20d, qa
# interest day] — references older than the window predate what the
# question is about. Unknown-dated refs (ref_time NULL) are kept: nothing
# places them outside the window.
REF_WINDOW_DAYS = 20


def in_ref_window(ref_time: Optional[datetime.datetime],
                  qa_day: datetime.date) -> bool:
    """Whether a ref's publish time falls inside the qa's interest window.

    *qa_day* is the question's own data date (qa_date's Asia/Shanghai
    day) — the date the question is about, not the time it was stored.
    """
    if ref_time is None:
        return True
    d = ref_time.astimezone(SHANGHAI_TZ).date()
    return qa_day - datetime.timedelta(days=REF_WINDOW_DAYS) <= d <= qa_day


# Structural publish dates in article URLs — separated path segments
# (…/articles/2026-09-30/4595816.html, …/2026/09/30/…) and compact id
# prefixes: the EastMoney article id (/a/202609111234567890.html — the
# id's first 8 digits are the publish date) and qq.com news ids
# (SN20260929181501a45db296 — a YYYYMMDDHHMMSS stamp). Month-only
# segments (…/202508/…) and bare long ids (m.toutiao.com/a68463…)
# deliberately do not match.
_URL_YMD_RE = re.compile(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})")
_URL_EASTMONEY_RE = re.compile(r"/a/(20\d{2})(\d{2})(\d{2})\d{6,}")
_URL_QQ_RE = re.compile(r"SN(20\d{2})(\d{2})(\d{2})\d{6,}")


def date_from_url(url: Optional[str], *,
                  today: Optional[datetime.date] = None) -> Optional[datetime.date]:
    """The article's publish date embedded in its URL, or None.

    Used as the second chance for hits the response carried no parseable
    date for — a fallback to now()/the qa date there writes a misleading
    text.news.date. Range-checked (a valid calendar date, not in the
    future); only explicit year+month+day shapes qualify.
    """
    if not url:
        return None
    m = (_URL_EASTMONEY_RE.search(url) or _URL_QQ_RE.search(url)
         or _URL_YMD_RE.search(url))
    if not m:
        return None
    try:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    if d > (today or datetime.datetime.now(SHANGHAI_TZ).date()):
        return None
    return d


def news_row_date(hit: SearchHit,
                  fallback_date: datetime.date) -> datetime.date:
    """The text.news ``date`` for a hit: the response's own publish date,
    else the URL-embedded date, else *fallback_date* (now()/qa date — the
    shape the AI page's ref dates must not display as news dates).

    One derivation shared by every writer/resolver so the (title, source,
    date) natural key stays identical across build_news_rows,
    _upsert_news_row and the re-find queries.
    """
    return hit.publish_date or date_from_url(hit.link) or fallback_date
