"""llm_agents.llm_qa_by_research.cli — the storage + generation driver.

Subcommands: ``generate`` (the six-request flow — searches, dimension
requests, the summary/label request, then one idempotent store),
``add`` / ``list`` / ``deactivate`` (direct row maintenance, the
llm_qa CLI shape).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import List, Optional

from _common.build_commons import get_db_or_exit
from _common.labels import RESEARCH_LOGIC_CODE, RESEARCH_LOGIC_LABEL
from _common.log_setup import setup_logging

from .generation import DEFAULT_CATEGORY, DEFAULT_SEARCH_COUNT, generate_research_qa
from .storage import LOGIC_LABELS, QA_TABLE

logger = setup_logging("llm_qa_by_research")


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
async def _run_tables_check(conn) -> None:
    for table in (QA_TABLE, "text.news_groups", "text.news_group_items",
                  "text.news"):
        schema, name = table.split(".", 1)
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_schema = $1 AND table_name = $2)", schema, name)
        if not exists:
            logger.error("    [FATAL] missing table %s — run "
                         "database/sql/text/*.sql first", table)
            sys.exit(1)


def _parse_news_ids(value: Optional[str]) -> Optional[List[int]]:
    if not value:
        return None
    try:
        ids = [int(v) for v in value.split(",") if v.strip()]
    except ValueError:
        logger.error("    [FATAL] --news-ids expects comma-separated ints")
        sys.exit(2)
    return ids or None


def _derive_sector(industry: Optional[str], sector: Optional[str]) -> Optional[str]:
    if sector is not None or industry is None:
        return sector
    from builds.text.keywords import get_taxonomy
    return get_taxonomy().sector_of(industry)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate + store research-logic Q&A rows in "
                    "text.llm_qa_by_research (six LLM requests per row).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser(
        "generate", help="run the six-request flow and store one row")
    p_gen.add_argument("--question", required=True,
                       help="The research question / subject (行业或标的的"
                            "底层研究问题).")
    p_gen.add_argument("--industry", type=str, default=None,
                       help="Canonical industry_id (BANKS, SEMI, …).")
    p_gen.add_argument("--sector", type=str, default=None,
                       help="Canonical sector_id (FIN, HC, …); derived from "
                            "the taxonomy when --industry is set and --sector "
                            "is not.")
    p_gen.add_argument("--category", type=str, default=DEFAULT_CATEGORY)
    p_gen.add_argument("--provider", type=str, default="zhipu",
                       help="Online-search provider name (registry key).")
    p_gen.add_argument("--model", type=str, default=None,
                       help="LLM model id; provider default when omitted.")
    p_gen.add_argument("--language", type=str, default="zh")
    p_gen.add_argument("--count", type=int, default=DEFAULT_SEARCH_COUNT,
                       help="Search hits per dimension request.")
    p_gen.add_argument("--recency", type=str, default="noLimit",
                       choices=("oneDay", "oneWeek", "oneMonth", "oneYear",
                                "noLimit"),
                       help="Search recency filter per dimension request.")
    p_gen.add_argument("--force", action="store_true",
                       help="Skip the quarterly cooldown: regenerate even "
                            "when the industry already has an ACTIVE row "
                            "generated within the last season.")

    p_add = sub.add_parser("add", help="insert/update one research Q&A row")
    p_add.add_argument("--question", required=True)
    p_add.add_argument("--answer", required=True,
                       help="The row's SUMMARY content (the display body).")
    p_add.add_argument("--logic-label", required=True, choices=LOGIC_LABELS,
                       help="Dominant investing logic of the row.")
    p_add.add_argument("--intro", type=str, default=None)
    p_add.add_argument("--optimistic-forecast", type=str, default=None)
    p_add.add_argument("--pessimistic-forecast", type=str, default=None)
    p_add.add_argument("--risks", type=str, default=None)
    p_add.add_argument("--capitalization", type=str, default=None)
    p_add.add_argument("--news-ids", type=str, default=None,
                       help="Comma-separated text.news ids proving the row.")
    p_add.add_argument("--industry", type=str, default=None)
    p_add.add_argument("--sector", type=str, default=None)
    p_add.add_argument("--category", type=str, default=None)
    p_add.add_argument("--model", type=str, default=None)
    p_add.add_argument("--language", type=str, default="zh")
    p_add.add_argument("--context-file", type=str, default=None)

    p_list = sub.add_parser("list", help="list research Q&A rows, newest first")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--all", action="store_true")
    p_list.add_argument("--industry", type=str, default=None)
    p_list.add_argument("--sector", type=str, default=None)
    p_list.add_argument("--logic-label", type=str, default=None,
                        choices=LOGIC_LABELS)

    p_off = sub.add_parser("deactivate", help="soft-delete one row")
    p_off.add_argument("--qa-id", type=int, required=True)

    args = ap.parse_args()

    conn = await get_db_or_exit()
    try:
        await _run_tables_check(conn)
        if args.cmd == "generate":
            from llm_agents.online_search_summary.providers.registry import (
                get_provider,
            )
            provider = get_provider(args.provider)()
            if not args.force:
                from .storage import RESEARCH_COOLDOWN_DAYS, \
                    industry_quarter_present
                present = await industry_quarter_present(conn, args.industry)
                if present is not None:
                    logger.info(
                        "skip: %s already generated %s (inside the %d-day "
                        "quarterly cooldown) — pass --force to regenerate",
                        args.industry, present.date(), RESEARCH_COOLDOWN_DAYS)
                    return
            qa_id = await generate_research_qa(
                conn, provider, args.question,
                industry_id=args.industry,
                sector_id=_derive_sector(args.industry, args.sector),
                model=args.model, lang=args.language,
                category=args.category,
                search_count=args.count, recency=args.recency)
            logger.info("stored text.llm_qa_by_research qa_id=%s", qa_id)
        elif args.cmd == "add":
            from .storage import upsert_qa_by_research

            context = None
            if args.context_file:
                with open(args.context_file, encoding="utf-8") as f:
                    context = f.read()
            qa_id = await upsert_qa_by_research(
                conn, args.question, args.answer,
                RESEARCH_LOGIC_CODE[args.logic_label],
                intro=args.intro,
                optimistic_forecast=args.optimistic_forecast,
                pessimistic_forecast=args.pessimistic_forecast,
                risks=args.risks, capitalization=args.capitalization,
                context=context, category=args.category,
                industry_id=args.industry,
                sector_id=_derive_sector(args.industry, args.sector),
                news_ids=_parse_news_ids(args.news_ids),
                llm_model=args.model, language=args.language)
            logger.info("stored text.llm_qa_by_research qa_id=%s", qa_id)
        elif args.cmd == "list":
            from .storage import fetch_qa_by_research

            rows = await fetch_qa_by_research(
                conn, limit=args.limit, include_inactive=args.all,
                industry_id=args.industry, sector_id=args.sector,
                logic_label=(RESEARCH_LOGIC_CODE[args.logic_label]
                             if args.logic_label else None))
            for r in rows:
                logger.info(
                    "#%s [%s] active=%s label=%s sources=%s "
                    "sector=%s industry=%s\n"
                    "  Q: %s\n  A: %s",
                    r["qa_id"],
                    r["updated_at"].date() if r["updated_at"] else "-",
                    "y" if r["is_active"] else "n",
                    RESEARCH_LOGIC_LABEL.get(r["logic_label"],
                                             r["logic_label"]),
                    r["n_sources"],
                    r["sector_id"] or "-", r["industry_id"] or "-",
                    r["question"][:80], (r["answer"] or "")[:100])
            logger.info("%d row(s)", len(rows))
        elif args.cmd == "deactivate":
            from .storage import deactivate_qa_by_research

            if await deactivate_qa_by_research(conn, args.qa_id):
                logger.info("deactivated qa_id=%s", args.qa_id)
            else:
                logger.warning("no row with qa_id=%s", args.qa_id)
                sys.exit(1)
    finally:
        from _common.db_commons import close_pools_async
        await close_pools_async()
