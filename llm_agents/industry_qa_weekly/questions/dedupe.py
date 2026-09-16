"""questions.dedupe — anchor parsing, the 60td same-direction dedupe
check against text.llm_qa, and the in-period hit filtering shared by the
compose retrieval path."""
from __future__ import annotations

import datetime
import re
from typing import List, Optional, Sequence

from llm_agents.online_search_summary.core.models import SearchHit

# The （截至…） suffix at the END of a stored question. No ^ anchor: the
# suffix sits at the end of the LONGER question, never at its start.
# Both spellings are parsed — the natural-Chinese form (written today)
# and the legacy ISO form (early test rows).
ANCHOR_RE_CN = re.compile(r"（截至(\d{4})年(\d{1,2})月(\d{1,2})日）$")
ANCHOR_RE_ISO = re.compile(r"（截至(\d{4})-(\d{2})-(\d{2})）$")

# Publish-lag slack + retrospective cutoff for the in-period hit filter:
# a roundup of the anchor week may hit the wire a few days later; anything
# much earlier is a retrospective, not coverage of the move.
DATE_FILTER_MARGIN = datetime.timedelta(days=3)
PERIOD_LOOKBACK = datetime.timedelta(days=200)


def parse_anchor(question: str) -> Optional[datetime.date]:
    """The （截至…） data date of a stored question, or None.

    Suffix-anchored (``$``, no ``^``): the date sits at the end of the
    longer question — a leading ``^`` can never match.
    """
    for rx in (ANCHOR_RE_CN, ANCHOR_RE_ISO):
        m = rx.search(question or "")
        if m:
            try:
                return datetime.date(*map(int, m.groups()))
            except ValueError:
                continue
    return None


def in_period(hits: Sequence[SearchHit], anchor: datetime.date,
              *, margin: datetime.timedelta = DATE_FILTER_MARGIN,
              lookback: datetime.timedelta = PERIOD_LOOKBACK,
              ) -> List[SearchHit]:
    """Keep the hits that could be period coverage, dated first:

    * dated hits published within [anchor - lookback, anchor + margin] —
      anything later is provably off-period (the wrong-year refs the
      stored answers were criticized for), anything much earlier is a
      retrospective, not coverage of the move;
    * undated hits last (no proof either way) — the caller drops them
      when the dated in-period coverage is already sufficient.
    """
    lo, hi = anchor - lookback, anchor + margin
    dated = [h for h in hits
             if h.publish_date is not None and lo <= h.publish_date <= hi]
    undated = [h for h in hits if h.publish_date is None]
    return dated + undated


def prefer_dated(hits: List[SearchHit], *, min_dated: int = 3,
                 ) -> List[SearchHit]:
    """Drop the undated hits once *min_dated* dated in-period hits exist —
    an undated hit's stored ref_time falls back to now(), which is exactly
    how wrong-year refs leaked into the corpus."""
    dated = [h for h in hits if h.publish_date is not None]
    return dated if len(dated) >= min_dated else hits


def merge_hits(*hit_lists: Sequence[SearchHit]) -> List[SearchHit]:
    """Concatenate hit lists, deduping by link (fallback title)."""
    seen: set = set()
    out: List[SearchHit] = []
    for hits in hit_lists:
        for h in hits:
            key = h.link or h.title
            if key not in seen:
                seen.add(key)
                out.append(h)
    return out


async def asked_within(
    conn, *, industry_id: str, base: str, anchor: datetime.date,
    window_start: datetime.date, window_end: datetime.date,
) -> bool:
    """True when *industry_id* already has a question starting with *base*
    (SAME industry, SAME rise/drop kind — the verb is part of the prefix,
    so a direction flip never matches and is always asked) anchored
    within the trading-date window [window_start, window_end] around the
    current anchor. Questions whose suffix cannot be parsed fall back to
    qa_date (manual rows)."""
    rows = await conn.fetch(ASKED_ANCHORS_SQL, industry_id, base)
    for r in rows:
        a = parse_anchor(r["question"]) or r["qa_date"].date()
        if window_start <= a <= window_end:
            return True
    return False


ASKED_ANCHORS_SQL = """
    SELECT question, (qa_date AT TIME ZONE 'Asia/Shanghai') AS qa_date
    FROM text.llm_qa
    WHERE industry_id = $1::text
      AND left(question, char_length($2::text)) = $2::text
"""
