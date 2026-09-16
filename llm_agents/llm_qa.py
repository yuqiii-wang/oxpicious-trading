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
        [--sector FIN] [--category macro] [--model gpt-…] [--language zh]
        [--context-file f]
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

# Answer-text refusal markers: the model produced no substantive
# explanation (its sources did not cover the period/move, so it answered
# with "cannot explain / no source support / will not fabricate"
# boilerplate — cf. "基于不编造信息的原则，无法提供导致大跌的实质性
# 原因要点。"). text.llm_qa.is_failed_explanation is derived from these
# at store time: any hit -> true, none -> false. 无法解释 has not
# surfaced in stored answers yet but guards the provider's phrasing.
FAILED_EXPLANATION_KEYWORDS = (
    "无法解释", "无法提供", "无法回答", "无法给出", "无法归纳",
    "无法为您", "无法依据", "无法根据", "无法从",
    "无法输出", "无法获取",
    "没有来源支持", "无来源支持",
    "不要编造", "不编造",
    "未能找到", "没有找到", "未找到", "未发现",
    "未提供", "未涉及", "未解释",
    "实质性原因", "没有资料解释",
)


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
    sector_id: Optional[str] = None,
    news_ids: Optional[Sequence[int]] = None,
    llm_model: Optional[str] = None,
    language: str = "zh",
    is_active: bool = True,
    qa_date: Optional[datetime.datetime] = None,
) -> int:
    """Insert/update one text.llm_qa row; return its qa_id.

    With *news_ids*, the sources are grouped (ensure_news_group) and linked.
    Uniqueness is (question, news_group_id); because Postgres UNIQUE treats
    NULLs as distinct, the news-less case is matched explicitly on
    question + NULL group and updated in place. *qa_date* is the
    question's OWN data date (the weekly industry Q&A's （截至…） anchor,
    Asia/Shanghai midnight) — the column that used to be created_at; the
    AI page maps it to the latest trading day on or before it. None keeps
    the now() default (manual/undated rows).

    is_failed_explanation is derived here from the answer text via
    FAILED_EXPLANATION_KEYWORDS: refusal boilerplate ("无法解释",
    "无法提供…" etc.) -> true; any substantive answer -> false.
    """
    if not question or not answer:
        raise ValueError("question and answer are required")

    failed_explanation = any(k in answer
                             for k in FAILED_EXPLANATION_KEYWORDS)

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
                f'category = $4, industry_id = $5, sector_id = $6, '
                f'llm_model = $7, language = $8, is_active = $9, '
                f'qa_date = COALESCE($10::timestamptz, qa_date), '
                f'is_failed_explanation = $11, '
                f'updated_at = now() '
                f'WHERE qa_id = $1',
                qa_id, answer, context, category, industry_id, sector_id,
                llm_model, language, is_active, qa_date,
                failed_explanation)
            return qa_id

    row = {
        "question": question,
        "answer": answer,
        "context": context,
        "category": category,
        "industry_id": industry_id,
        "sector_id": sector_id,
        "news_group_id": news_group_id,
        "llm_model": llm_model,
        "language": language,
        "is_active": is_active,
        "is_failed_explanation": failed_explanation,
    }
    if qa_date is not None:
        row["qa_date"] = qa_date
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
    sector_id: Optional[str] = None,
) -> List[dict]:
    """Newest-first Q&A rows (joined with the source-group article count)."""
    rows = await conn.fetch(
        f'SELECT q.qa_id, q.question, q.answer, q.category, q.industry_id, '
        f'q.sector_id, '
        f'q.news_group_id, q.llm_model, q.language, q.is_active, '
        f'q.qa_date, q.updated_at, '
        f'(SELECT COUNT(*) FROM {GROUP_ITEMS_TABLE} gi '
        f' WHERE gi.news_group_id = q.news_group_id) AS n_sources '
        f'FROM {QA_TABLE} q '
        f'WHERE ($1 OR q.is_active) '
        f'  AND ($2::text IS NULL OR q.industry_id = $2) '
        f'  AND ($3::text IS NULL OR q.sector_id = $3) '
        f'ORDER BY q.updated_at DESC LIMIT $4',
        include_inactive, industry_id, sector_id, limit)
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
    p_add.add_argument("--sector", type=str, default=None,
                       help="Canonical sector_id (FIN, HC, …); derived from "
                            "the taxonomy when --industry is set and --sector "
                            "is not.")
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
    p_list.add_argument("--sector", type=str, default=None)

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
            sector_id = args.sector
            if sector_id is None and args.industry:
                from builds.text.keywords import get_taxonomy
                sector_id = get_taxonomy().sector_of(args.industry)
            qa_id = await upsert_qa(
                conn, args.question, args.answer,
                context=context, category=args.category,
                industry_id=args.industry, sector_id=sector_id,
                news_ids=_parse_news_ids(args.news_ids),
                llm_model=args.model, language=args.language)
            logger.info("stored text.llm_qa qa_id=%s", qa_id)
        elif args.cmd == "list":
            rows = await fetch_qa(conn, limit=args.limit,
                                  include_inactive=args.all,
                                  industry_id=args.industry,
                                  sector_id=args.sector)
            for r in rows:
                logger.info(
                    "#%s [%s] active=%s sources=%s sector=%s industry=%s\n"
                    "  Q: %s\n  A: %s",
                    r["qa_id"], r["updated_at"].date() if r["updated_at"] else "-",
                    "y" if r["is_active"] else "n", r["n_sources"],
                    r["sector_id"] or "-", r["industry_id"] or "-",
                    r["question"][:80], (r["answer"] or "")[:100])
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
