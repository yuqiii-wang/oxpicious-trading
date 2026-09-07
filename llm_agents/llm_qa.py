"""llm_agents.llm_qa — Storage layer for the LLM Q&A knowledge base.

Persists curated question/answer documents into ``text.llm_qa`` (canonical
DDL: database/sql/text/02_llm_qa.sql) together with their news provenance:
the source-article news_ids are grouped into one ``text.news_groups`` row,
linked through ``text.news_group_items``, and the group is referenced by
``text.llm_qa.news_group_id``.

This module is deliberately storage-only — how agents should retrieve
context and generate answers is not implemented yet. The write API is
idempotent per (question, news_group):

  * ``upsert_qa``      — insert or update one Q&A row (+ optional group).
  * ``deactivate_qa``  — soft-delete (is_active = false).
  * ``fetch_qa``       — list active rows, optionally by industry/category.

CLI (``python -m llm_agents.llm_qa``)::

    add --question "…" --answer "…" [--news-ids 1,2,3] [--industry BANKS]
        [--category macro] [--model gpt-…] [--language zh] [--context-file f]
    list [--limit 20] [--all]           # active (or all) rows, newest first
    deactivate --qa-id 42
"""
from __future__ import annotations

# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()

import argparse
import asyncio
import datetime
import sys
from typing import List, Optional, Sequence

from _common.build_commons import get_db_or_exit, setup_utf8_stdout

setup_utf8_stdout()

from _common.build_commons import bulk_upsert_async  # noqa: E402
from _common.log_setup import setup_logging  # noqa: E402

logger = setup_logging("llm_qa")

QA_TABLE = "text.llm_qa"
GROUPS_TABLE = "text.news_groups"
GROUP_ITEMS_TABLE = "text.news_group_items"


# ----------------------------------------------------------------------------
# News provenance group
# ----------------------------------------------------------------------------
async def ensure_news_group(
    conn,
    news_ids: Sequence[int],
    name: Optional[str] = None,
) -> int:
    """Create a text.news_groups row + items for *news_ids*, return its id.

    Idempotent on the (news_group_id, news_id) junction (ON CONFLICT DO
    NOTHING), so re-storing an answer with the same sources reuses the new
    group without duplication. News ids must already exist in text.news
    (FK) — run builds.text first.
    """
    if not news_ids:
        raise ValueError("news_ids must be non-empty when linking sources")
    if name is None:
        name = f"qa-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    group_id = await conn.fetchval(
        f'INSERT INTO {GROUPS_TABLE} (news_group_name) VALUES ($1) '
        f'RETURNING news_group_id', name)
    await conn.executemany(
        f'INSERT INTO {GROUP_ITEMS_TABLE} (news_group_id, news_id) '
        f'VALUES ($1, $2) ON CONFLICT DO NOTHING',
        [(group_id, int(nid)) for nid in news_ids])
    return group_id


# ----------------------------------------------------------------------------
# Q&A rows
# ----------------------------------------------------------------------------
async def upsert_qa(
    conn,
    question: str,
    answer: str,
    *,
    context: Optional[str] = None,
    category: Optional[str] = None,
    industry_id: Optional[str] = None,
    news_ids: Optional[Sequence[int]] = None,
    llm_model: Optional[str] = None,
    language: str = "zh",
    is_active: bool = True,
) -> int:
    """Insert/update one text.llm_qa row; return its qa_id.

    With *news_ids*, the sources are grouped (ensure_news_group) and linked.
    Uniqueness is (question, news_group_id); because Postgres UNIQUE treats
    NULLs as distinct, the news-less case is matched explicitly on
    question + NULL group and updated in place.
    """
    if not question or not answer:
        raise ValueError("question and answer are required")

    news_group_id: Optional[int] = None
    if news_ids:
        news_group_id = await ensure_news_group(
            conn, news_ids, name=f"qa: {question[:60]}")

    if news_group_id is None:
        qa_id = await conn.fetchval(
            f'SELECT qa_id FROM {QA_TABLE} '
            f'WHERE question = $1 AND news_group_id IS NULL',
            question)
        if qa_id is not None:
            await conn.execute(
                f'UPDATE {QA_TABLE} SET answer = $2, context = $3, '
                f'category = $4, industry_id = $5, llm_model = $6, '
                f'language = $7, is_active = $8, updated_at = now() '
                f'WHERE qa_id = $1',
                qa_id, answer, context, category, industry_id,
                llm_model, language, is_active)
            return qa_id

    row = {
        "question": question,
        "answer": answer,
        "context": context,
        "category": category,
        "industry_id": industry_id,
        "news_group_id": news_group_id,
        "llm_model": llm_model,
        "language": language,
        "is_active": is_active,
    }
    await bulk_upsert_async(conn, QA_TABLE, [row], ["question", "news_group_id"])
    qa_id = await conn.fetchval(
        f'SELECT qa_id FROM {QA_TABLE} '
        f'WHERE question = $1 AND news_group_id IS NOT DISTINCT FROM $2',
        question, news_group_id)
    return qa_id


async def deactivate_qa(conn, qa_id: int) -> bool:
    """Soft-delete: is_active = false. Returns True when a row matched."""
    status = await conn.execute(
        f'UPDATE {QA_TABLE} SET is_active = false, updated_at = now() '
        f'WHERE qa_id = $1', qa_id)
    return status == "UPDATE 1"


async def fetch_qa(
    conn,
    limit: int = 20,
    include_inactive: bool = False,
    industry_id: Optional[str] = None,
) -> List[dict]:
    """Newest-first Q&A rows (joined with the source-group article count)."""
    rows = await conn.fetch(
        f'SELECT q.qa_id, q.question, q.answer, q.category, q.industry_id, '
        f'q.news_group_id, q.llm_model, q.language, q.is_active, '
        f'q.created_at, q.updated_at, '
        f'(SELECT COUNT(*) FROM {GROUP_ITEMS_TABLE} gi '
        f' WHERE gi.news_group_id = q.news_group_id) AS n_sources '
        f'FROM {QA_TABLE} q '
        f'WHERE ($1 OR q.is_active) '
        f'  AND ($2::text IS NULL OR q.industry_id = $2) '
        f'ORDER BY q.updated_at DESC LIMIT $3',
        include_inactive, industry_id, limit)
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------------
# CLI — storage driver only (no agent logic yet)
# ----------------------------------------------------------------------------
async def _run_tables_check(conn) -> None:
    for table in (QA_TABLE, GROUPS_TABLE, GROUP_ITEMS_TABLE, "text.news"):
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


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Store Q&A knowledge-base rows in text.llm_qa "
                    "(agent orchestration comes later).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="insert/update one Q&A row")
    p_add.add_argument("--question", required=True)
    p_add.add_argument("--answer", required=True)
    p_add.add_argument("--news-ids", type=str, default=None,
                       help="Comma-separated text.news ids proving the answer.")
    p_add.add_argument("--industry", type=str, default=None,
                       help="Canonical industry_id (BANKS, SEMI, …).")
    p_add.add_argument("--category", type=str, default=None)
    p_add.add_argument("--model", type=str, default=None,
                       help="LLM model id that produced the answer.")
    p_add.add_argument("--language", type=str, default="zh")
    p_add.add_argument("--context-file", type=str, default=None,
                       help="File whose contents become the context column.")

    p_list = sub.add_parser("list", help="list Q&A rows, newest first")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--all", action="store_true",
                        help="include inactive rows")
    p_list.add_argument("--industry", type=str, default=None)

    p_off = sub.add_parser("deactivate", help="soft-delete one row")
    p_off.add_argument("--qa-id", type=int, required=True)

    args = ap.parse_args()

    conn = await get_db_or_exit()
    try:
        await _run_tables_check(conn)
        if args.cmd == "add":
            context = None
            if args.context_file:
                with open(args.context_file, encoding="utf-8") as f:
                    context = f.read()
            qa_id = await upsert_qa(
                conn, args.question, args.answer,
                context=context, category=args.category,
                industry_id=args.industry,
                news_ids=_parse_news_ids(args.news_ids),
                llm_model=args.model, language=args.language)
            logger.info("stored text.llm_qa qa_id=%s", qa_id)
        elif args.cmd == "list":
            rows = await fetch_qa(conn, limit=args.limit,
                                  include_inactive=args.all,
                                  industry_id=args.industry)
            for r in rows:
                logger.info(
                    "#%s [%s] active=%s sources=%s industry=%s\n  Q: %s\n  A: %s",
                    r["qa_id"], r["updated_at"].date() if r["updated_at"] else "-",
                    "y" if r["is_active"] else "n", r["n_sources"],
                    r["industry_id"] or "-", r["question"][:80],
                    (r["answer"] or "")[:100])
            logger.info("%d row(s)", len(rows))
        elif args.cmd == "deactivate":
            if await deactivate_qa(conn, args.qa_id):
                logger.info("deactivated qa_id=%s", args.qa_id)
            else:
                logger.warning("no row with qa_id=%s", args.qa_id)
                sys.exit(1)
    finally:
        from _common.db_commons import close_pools_async
        await close_pools_async()


if __name__ == "__main__":
    asyncio.run(main())
