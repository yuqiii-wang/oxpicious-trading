"""questions — question construction + dedupe for the sudden-move Q&A.

* ``build``  — question phrasing (base + （截至…） suffix), search-query
  construction, and the anchor->recency-window mapping.
* ``dedupe`` — anchor parsing, the same-direction dedupe check against
  text.llm_qa, and the in-period hit filtering for compose retrieval.
"""
from llm_agents.industry_qa_weekly.questions.build import (  # noqa: F401
    DATE_SUFFIX_FMT, MARKET_INDEX_LABEL, SIDE_VERBS, build_market_question,
    build_question, market_question_base, market_search_query,
    market_widen_query, period_search_query, period_widen_query,
    question_base, recency_for_anchor,
)
from llm_agents.industry_qa_weekly.questions.dedupe import (  # noqa: F401
    ANCHOR_RE_CN, ANCHOR_RE_ISO, ASKED_ANCHORS_SQL, DATE_FILTER_MARGIN,
    PERIOD_LOOKBACK, asked_within, in_period, merge_hits, parse_anchor,
    prefer_dated,
)
