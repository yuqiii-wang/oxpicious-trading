"""llm_agents._core.text_caps — store-time length caps for generated text.

LLM output and the context passages it was grounded in are bounded at
store time so the text.* tables (and the API payloads that read them)
cannot grow without limit. The caps mirror the one-time cleanup in
database/sql/text/10_llm_summary_caps.sql:

  * MAX_SUMMARY_CHARS — answer-like columns the UI renders (llm_qa.answer,
    llm_qa_by_ask.answer, the five research dimension texts, error tails).
  * MAX_CONTEXT_CHARS — the supporting-passage ``context`` columns, which
    the UI never renders (data_viz AiQaCard) but the detail endpoints
    ship whole — these held the measured extremes (max 152K chars).
"""
from __future__ import annotations

from typing import Optional

MAX_SUMMARY_CHARS = 2000
MAX_CONTEXT_CHARS = 8000

# Appended when a cap bites — same marker as the news-body truncation in
# llm_agents.news_digestions.agent.truncate_content.
TRUNCATION_SUFFIX = " …[截断]"


def truncate_text(text: Optional[str], max_chars: int,
                  *, suffix: str = TRUNCATION_SUFFIX) -> Optional[str]:
    """Clip *text* to *max_chars* chars, appending the marker when cut."""
    if text is None or len(text) <= max_chars:
        return text
    return text[:max_chars] + suffix


def truncate_summary(text: Optional[str]) -> Optional[str]:
    """Cap answer-like UI text at MAX_SUMMARY_CHARS."""
    return truncate_text(text, MAX_SUMMARY_CHARS)


def truncate_context(text: Optional[str]) -> Optional[str]:
    """Cap supporting-passage text at MAX_CONTEXT_CHARS."""
    return truncate_text(text, MAX_CONTEXT_CHARS)
