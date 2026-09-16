"""llm_agents.online_search_summary.core — Pure domain layer.

Provider-independent contracts: normalized result models, citation
handling, summary prompts, and the error type. NO network, NO DB, and NO
imports from the providers/storage/cli layers — the dependency direction
is providers/storage/cli -> core, never back.

  * ``errors``    — OnlineSearchError (status + provider error code).
  * ``models``    — SearchHit / SearchIntent / SearchOptions /
    SearchResponse / SearchSummary, the request vocabulary
    (RECENCY_FILTERS / CONTENT_SIZES) and SHANGHAI_TZ.
  * ``citations`` — the refer/citation/date helpers (normalize_refer,
    parse_publish_date, extract_cited_refs, format_references).
  * ``prompts``   — the summary output contract and its prompt builders
    (SUMMARY_FORMAT_RULES, summary_system_prompt, native_search_prompt,
    lang_label).
"""
from __future__ import annotations

from llm_agents.online_search_summary.core.errors import OnlineSearchError
from llm_agents.online_search_summary.core.citations import (
    REF_TAG_RE, extract_cited_refs, format_references, normalize_refer,
    parse_publish_date,
)
from llm_agents.online_search_summary.core.models import (
    CONTENT_SIZES, RECENCY_FILTERS, SHANGHAI_TZ, SearchHit, SearchIntent,
    SearchOptions, SearchResponse, SearchSummary,
)
from llm_agents.online_search_summary.core.prompts import (
    LANG_LABELS, SUMMARY_FORMAT_RULES, lang_label, native_search_prompt,
    summary_system_prompt,
)

__all__ = [
    "OnlineSearchError",
    "SearchHit", "SearchIntent", "SearchOptions", "SearchResponse",
    "SearchSummary", "RECENCY_FILTERS", "CONTENT_SIZES", "SHANGHAI_TZ",
    "REF_TAG_RE", "normalize_refer", "parse_publish_date",
    "extract_cited_refs", "format_references",
    "LANG_LABELS", "SUMMARY_FORMAT_RULES", "lang_label",
    "native_search_prompt", "summary_system_prompt",
]
