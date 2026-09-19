"""llm_agents.online_search_summary.storage — text-schema persistence layer.

Everything that writes the agent's outcome into the ``text`` schema, plus
the per-reference resolution that decides WHERE each citation lands:

  * ``matching`` — the ref_type/resolved_via vocabulary and the pure
    text/URL/time helpers (normalize_text, is_exact_article,
    source_from_url, hit_ref_time).
  * ``fetching`` — PageFetcher: ddgs title search + size-capped page
    fetch + markitdown extraction (the verification network calls).
  * ``resolve``  — RefResolver: corpus match -> ddgs + markitdown
    verification -> null-content placeholder; one ResolvedRef per
    citation tag. ``build_ref_rows`` projects them onto the
    text.llm_qa_refs rows (one per resolved article — representative
    ref tag + is_used).
  * ``store``    — OnlineSearchSummaryStore: references -> text.news, the
    per-ref typing -> text.llm_qa_refs, the Q&A -> text.llm_qa.

Depends only on ``core`` (+ project _common / builds.text utilities);
never on ``providers``.
"""
from __future__ import annotations

from llm_agents.online_search_summary.storage.matching import (
    REF_TYPE_EXACT, REF_TYPE_RELEVANT, VIA_CORPUS, VIA_DDGS, VIA_SUMMARY,
    hit_ref_time, is_exact_article, normalize_text, source_from_url,
)
from llm_agents.online_search_summary.storage.fetching import PageFetcher
from llm_agents.online_search_summary.storage.resolve import (
    NEWS_TABLE, RefResolver, ResolvedRef, build_ref_rows,
)
from llm_agents.online_search_summary.storage.store import (
    OnlineSearchSummaryStore,
)

__all__ = [
    "REF_TYPE_EXACT", "REF_TYPE_RELEVANT",
    "VIA_SUMMARY", "VIA_CORPUS", "VIA_DDGS",
    "normalize_text", "is_exact_article", "source_from_url", "hit_ref_time",
    "PageFetcher",
    "NEWS_TABLE", "RefResolver", "ResolvedRef", "build_ref_rows",
    "OnlineSearchSummaryStore",
]
