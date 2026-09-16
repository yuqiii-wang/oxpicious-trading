"""questions.build — question phrasing + search-query construction.

The question follows the manually-asked convention visible in text.llm_qa
(e.g. ``银行板块近期走强的原因``): a Chinese "why did this industry move"
question, one per (industry, side), with the move's data date as the
（截至…） suffix:

    为什么{label}板块近期大涨？（截至2025年9月12日）   # HYPE
    为什么{label}板块近期大跌？（截至2025年9月12日）   # DRAIN

The suffix is written in natural Chinese 年月日 tokens — the form news
archives are indexed by and the form the model re-uses when it
formulates its own search queries — and anchors the dedupe (the date is
parsed back out of the stored question).

RECENCY — recency_for_anchor maps the anchor to the web_search API's
only date knob, the relative search_recency_filter: an anchor within the
last week gets ``oneWeek``, within a month ``oneMonth``; older anchors
keep ``noLimit`` — historical asks rely on the 年月日 tokens instead.
"""
from __future__ import annotations

import datetime
from typing import Optional

SIDE_VERBS = {"HYPE": "大涨", "DRAIN": "大跌"}

MARKET_INDEX_LABEL = "上证指数"

# Sudden moves are recent by construction (MA5-slope deviation from the
# benchmark), so questions uniformly read 近期 — no per-window phrase tiers.

DATE_SUFFIX_FMT = "（截至{as_of.year}年{as_of.month}月{as_of.day}日）"


def _verb(side: str) -> str:
    try:
        return SIDE_VERBS[side]
    except KeyError:
        raise ValueError(f"unknown rank side {side!r} "
                         f"(known: {sorted(SIDE_VERBS)})") from None


def market_question_base(side: str) -> str:
    """Date-less market question prefix — the dedupe key (with the index's
    parent industry_id); the verb is part of the prefix so a direction
    flip never matches."""
    return f"为什么{MARKET_INDEX_LABEL}大盘近期{SIDE_VERBS[side]}？"


def build_market_question(side: str, as_of: datetime.date) -> str:
    return market_question_base(side) + DATE_SUFFIX_FMT.format(as_of=as_of)


def market_search_query(side: str, anchor: datetime.date,
                        label: str = MARKET_INDEX_LABEL) -> str:
    """Leading year-month tokens, same measured convention as
    period_search_query — big index days are heavily covered, so the
    month + index label + verb recalls period news reliably."""
    return (f"{anchor.year}年{anchor.month}月 {label} 大盘 "
            f"{SIDE_VERBS[side]}")


def market_widen_query(anchor: datetime.date,
                       label: str = MARKET_INDEX_LABEL) -> str:
    return f"{anchor.year}年{anchor.month}月 {label}"


def period_search_query(industry_label: str, side: str,
                        anchor: datetime.date) -> str:
    """Standalone-search query for a historical ask. LEADING year-month
    tokens are the strongest period signal: measured against ZhiPu's
    web search, ``2025年9月 创新药 板块 大跌`` returned 9/10 in-period
    hits while every industry-first phrasing of the same question
    returned 10/10 CURRENT (2026) articles — the engine weights leading
    tokens heavily, so the year-month must come first. (The full
    QUESTION, with its uniform 近期 phrasing, is for the summarizer,
    not the engine.)"""
    return (f"{anchor.year}年{anchor.month}月 {industry_label}板块 "
            f"{SIDE_VERBS[side]}")


def period_widen_query(industry_label: str,
                       anchor: datetime.date) -> str:
    """The widen retry when the primary query recalls too few in-period
    hits: same leading year-month, verb and cause-word dropped."""
    return f"{anchor.year}年{anchor.month}月 {industry_label}板块"


def question_base(industry_label: str, side: str) -> str:
    """The date-less question prefix — also the dedupe key (with the
    industry_id) for the look-back window. The move is sudden (deviation
    from the benchmark), so the window phrase is a uniform 近期."""
    return f"为什么{industry_label}板块近期{_verb(side)}？"


def build_question(industry_label: str, side: str, as_of: datetime.date) -> str:
    """The full question: base + the （截至…年…月…日） data-date suffix."""
    return (question_base(industry_label, side)
            + DATE_SUFFIX_FMT.format(as_of=as_of))


def recency_for_anchor(anchor: datetime.date,
                       today: Optional[datetime.date] = None) -> str:
    """Map the question's anchor date to a web_search recency filter.

    The API has no absolute date range — only relative windows counted
    from NOW. So an anchor that is essentially CURRENT (the live weekly
    run: the event date is a few days old) gets a hard-scoped window
    covering its own week/month, while historical (backfill) anchors keep
    ``noLimit``: a relative window older than the anchor could only
    exclude period coverage, and the 年月日 tokens in the question are
    the period signal instead.
    """
    today = today or datetime.date.today()
    days = max(0, (today - anchor).days)
    if days <= 7:
        return "oneWeek"
    if days <= 31:
        return "oneMonth"
    return "noLimit"
