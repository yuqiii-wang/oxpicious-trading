"""llm_agents.industry_qa_weekly — Weekly industry hypes & drains Q&A.

Every week, pick the industries that moved the market most over the last
5 trading days — the top-5 HYPE (rose a lot vs the benchmark) and top-5
DRAIN (dropped a lot) from analysis.industry_hypes_seasonal at the
Market Trend view's default period (120 trading days ≈ half a year) —
the exact monthly block the "Market Trend → Hypes & Drains" view
renders — and for each one ask the online-search summarize agent WHY
(the manually-asked convention in text.llm_qa, e.g.
``银行板块近期走强的原因``), with the ranking's data date added to the
question:

    为什么云计算板块近半年大涨？（截至2025年11月3日）
    为什么白酒板块近半年大跌？（截至2025年11月3日）

Each Q&A is stored into text.llm_qa (category='industry', industry_id
pinned, refs resolved into text.news + text.llm_qa_refs), and an
industry whose same-kind question was already anchored within the last
60 ranking dates (trading days) is skipped, while a direction flip
(rise then drop, or drop then rise) is always asked. Anchors inside
the CURRENT month bypass the skip entirely and use a recent-movements
question (drivers + fund flows), so the live week is always captured
fresh.

Two AI-QA flows share the one knowledge base (text.llm_qa) — this
package is the HISTORY flow: episode-driven asks on the seasonal top +
MA5-slope deviation signals, backfilled over the trading calendar
(categories ``industry`` / ``market``). The RECENCY flow is
``downloads.macro.ai_daily`` — today / latest-biz-date broad-market
summary + the latest cross_stats movers (category ``market_daily``);
it never backfills, and this package's plan-first backfill never asks
about the live daily snapshot.

Package layout (small files, llm_agents-style):

  * ``signals``   — read the top-N HYPE/DRAIN industries for one
    (benchmark, period=5, weighting, as-of) from
    analysis.industry_hypes_and_drains, plus the full ranking-date list
    the backfill grid steps on.
  * ``questions`` — the question template (base + （截至date） suffix),
    the anchor parser, and the 60-trading-day same-direction dedupe
    check against
    text.llm_qa.
  * ``agent``     — run_week (signals -> dedupe -> ask -> store) and
    run_backfill (the weekly step replayed on the 5-trading-day grid —
    every step's window disjoint from the previous — oldest first, so
    dedupe behaves like real weekly runs).
  * ``cli``       — argparse CLI (run / backfill, --dry-run, --limit).

CLI::

    python -m llm_agents.industry_qa_weekly run [--dry-run]
    python -m llm_agents.industry_qa_weekly backfill \
        --start 2025-09-13 [--end 2026-09-13] [--dry-run]

(``python -m llm_agents industry-qa …`` dispatches here too, see
llm_agents.__main__.)

The package re-exports its public surface so ``from
llm_agents.industry_qa_weekly import X`` keeps working regardless of
which file X lives in.
"""
from __future__ import annotations

from llm_agents.industry_qa_weekly.signals import (
    DEFAULT_BENCHMARK, DEFAULT_PERIOD_DAYS, DEFAULT_TOP_N,
    DEFAULT_WEIGHTING, IndustrySignal, fetch_market_episodes,
    iter_industry_episodes, latest_signal_date,
)
from llm_agents.industry_qa_weekly.questions import (
    build_question, build_market_question, in_period, merge_hits,
    parse_anchor, period_search_query, period_widen_query,
    prefer_dated, question_base, recency_for_anchor,
)
from llm_agents.industry_qa_weekly.core.agent import (
    CATEGORY, DEFAULT_DEDUPE_WINDOW, DEFAULT_INTERVAL, AskOutcome,
    PlanTooLargeError, run_backfill, run_week,
)
from llm_agents.industry_qa_weekly.cli import main

__all__ = [
    "DEFAULT_BENCHMARK", "DEFAULT_PERIOD_DAYS", "DEFAULT_TOP_N",
    "DEFAULT_WEIGHTING", "DEFAULT_INTERVAL", "DEFAULT_DEDUPE_WINDOW",
    "IndustrySignal", "fetch_ranking_dates", "fetch_top_industries",
    "latest_signal_date", "iter_industry_episodes",
    "fetch_market_episodes", "fetch_index_parent_tags",
    "build_question", "build_market_question", "parse_anchor",
    "question_base", "market_question_base", "recency_for_anchor",
    "asked_within", "period_search_query", "period_widen_query",
    "in_period", "prefer_dated", "merge_hits",
    "CATEGORY", "AskOutcome", "PlanTooLargeError", "run_week",
    "run_backfill", "main",
]
