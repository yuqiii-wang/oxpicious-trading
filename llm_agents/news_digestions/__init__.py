"""llm_agents.news_digestions — Per-article LLM digestion agent.

Summarizes every text.news article and scores its sentiment in [-5, 5],
persisting one row per article into ``text.news_digestions`` (canonical
DDL: database/sql/text/04_news_digestions.sql). The LLM call is delegated
to a registered online-search provider's plain chat completion (no web
search involved); digestions are content-only.

Package layout (small files, builds.text-style):

  * ``agent`` — the digestion prompt (Chinese analyst: 2-4 sentence
    summary + strict-JSON ``{"summary", "sentiment"}`` contract),
    fence-tolerant parsing with the score clamped to [-5, 5] (the DDL
    CHECK), and ``digest_article`` (one article -> one row dict, one
    parse-retry).
  * ``store`` — the work queue (articles missing a digestion row; title-
    only placeholders skipped unless asked) and the (news_id)-keyed
    upsert into text.news_digestions.
  * ``cli``   — argparse CLI (run / list, date/industry/source windows,
    --force, --dry-run, --concurrency).

CLI::

    python -m llm_agents.news_digestions run [--limit 100] [--force] \
        [--start-date …] [--end-date …] [--industry BANKS] [--source gov]
    python -m llm_agents.news_digestions list [--limit 20]

(``python -m llm_agents digest|digestions …`` dispatches here too, see
llm_agents.__main__.)

The package re-exports its public surface so ``from
llm_agents.news_digestions import X`` keeps working regardless of which
file X lives in.
"""
from __future__ import annotations

from llm_agents.news_digestions.agent import (
    DigestionError, build_digest_messages, digest_article,
    digest_system_prompt, parse_digest_json, truncate_content,
)
from llm_agents.news_digestions.store import (
    DIGESTIONS_TABLE, count_pending, fetch_digestions,
    fetch_pending_articles, upsert_digestions,
)
from llm_agents.news_digestions.cli import main

__all__ = [
    "DigestionError",
    "digest_system_prompt", "build_digest_messages", "parse_digest_json",
    "truncate_content", "digest_article",
    "DIGESTIONS_TABLE", "fetch_pending_articles", "upsert_digestions",
    "fetch_digestions", "count_pending",
    "main",
]
