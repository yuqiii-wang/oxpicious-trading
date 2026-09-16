"""llm_agents.news_digestions.cli — argparse CLI.

Usage::

    python -m llm_agents.news_digestions run [--limit 100] \
        [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] \
        [--industry BANKS] [--source gov] [--force] [--include-empty] \
        [--provider zhipu] [--model glm-5.2] [--concurrency 4] \
        [--max-content-chars 8000] [--lang zh] [--dry-run]
    python -m llm_agents.news_digestions list [--limit 20] [--industry BANKS]

``python -m llm_agents digest|digestions …`` dispatches here too (see
llm_agents.__main__). ``run`` digests every in-scope article that has no
text.news_digestions row yet (--force re-digests all in-scope articles,
overwriting), writing each row as its LLM call completes so a crash never
loses completed work; ``--dry-run`` just lists the work queue.
"""
from __future__ import annotations

# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()

from _common.build_commons import (  # noqa: E402
    get_db_or_exit, setup_utf8_stdout,
)

setup_utf8_stdout()

import argparse  # noqa: E402
import asyncio  # noqa: E402
import datetime  # noqa: E402
import sys  # noqa: E402
from typing import Optional  # noqa: E402

from _common.log_setup import setup_logging  # noqa: E402

from llm_agents.news_digestions.agent import digest_article  # noqa: E402
from llm_agents.news_digestions.store import (  # noqa: E402
    count_pending, fetch_digestions, fetch_pending_articles,
    upsert_digestions,
)
from llm_agents.online_search_summary import (  # noqa: E402
    PROVIDERS, get_provider,
)

logger = setup_logging("news_digestions")


async def _run_tables_check(conn) -> None:
    for table in ("text.news", "text.news_digestions"):
        schema, name = table.split(".", 1)
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_schema = $1 AND table_name = $2)", schema, name)
        if not exists:
            logger.error("    [FATAL] missing table %s — run "
                         "database/sql/text/*.sql first", table)
            sys.exit(1)


def _parse_date_arg(value: Optional[str], flag: str
                    ) -> Optional[datetime.date]:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        logger.error("    [FATAL] invalid %s (expected YYYY-MM-DD): %s",
                     flag, value)
        sys.exit(2)


def _fmt_sentiment(level: Optional[float]) -> str:
    return f"{level:+.1f}" if level is not None else "  n/a"


async def _run(args) -> None:
    conn = await get_db_or_exit()
    try:
        await _run_tables_check(conn)
        articles = await fetch_pending_articles(
            conn,
            start=_parse_date_arg(args.start_date, "--start-date"),
            end=_parse_date_arg(args.end_date, "--end-date"),
            industry_id=args.industry, source=args.source,
            include_empty=args.include_empty, force=args.force,
            limit=args.limit or None)
        if not articles:
            logger.info("    no articles in scope — nothing to digest")
            return
        span = f"{articles[-1]['date']} → {articles[0]['date']}"
        logger.info("    %d article(s) in scope (%s)%s", len(articles),
                    span, ", force (overwrite existing)" if args.force
                    else "")

        if args.dry_run:
            for a in articles:
                logger.info("    news_id=%-8s %s [%s/%s] %s",
                            a["news_id"], a["date"], a["source"] or "-",
                            a["industry_id"] or "-", a["title"][:70])
            logger.info("%d article(s) would be digested", len(articles))
            return

        provider = get_provider(args.provider)()
        model = args.model or getattr(provider, "DEFAULT_MODEL", None)
        if not model:
            logger.error("    [FATAL] no chat model — pass --model")
            sys.exit(2)
        logger.info("    digesting with %s/%s (concurrency=%d) …",
                    provider.name, model, args.concurrency)

        sem = asyncio.Semaphore(max(1, args.concurrency))

        async def run_one(article):
            async with sem:
                return await digest_article(
                    provider, article, model=model, lang=args.lang,
                    max_content_chars=args.max_content_chars)

        ok = failed = 0
        for fut in asyncio.as_completed([run_one(a) for a in articles]):
            try:
                row = await fut
            except Exception as e:  # one bad article never kills the run
                failed += 1
                logger.error("    [digest] failed: %s: %s",
                             type(e).__name__, e)
                continue
            await upsert_digestions(conn, [row])
            ok += 1
            logger.info("    [%d/%d] news_id=%s sentiment=%s %s",
                        ok + failed, len(articles), row["news_id"],
                        _fmt_sentiment(row["sentiment_level"]),
                        row["summary"][:60])

        pending = await count_pending(conn)
        logger.info("    [DB] %d digested, %d failed; %d article(s) still "
                    "pending", ok, failed, pending)
    finally:
        from _common.db_commons import close_pools_async
        await close_pools_async()


async def _list(args) -> None:
    conn = await get_db_or_exit()
    try:
        await _run_tables_check(conn)
        rows = await fetch_digestions(conn, limit=args.limit,
                                      industry_id=args.industry)
        for r in rows:
            logger.info(
                "#%s %s [%s/%s] sentiment=%s model=%s\n    %s\n    %s",
                r["news_id"], r["date"], r["source"] or "-",
                r["industry_id"] or "-", _fmt_sentiment(r["sentiment_level"]),
                r["llm_model"] or "-", r["title"][:80],
                (r["summary"] or "")[:120])
        logger.info("%d row(s)", len(rows))
    finally:
        from _common.db_commons import close_pools_async
        await close_pools_async()


async def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m llm_agents.news_digestions",
        description="Per-article LLM digestion agent: summarize every "
                    "text.news article and score its sentiment in [-5, 5] "
                    "-> text.news_digestions.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # "digest" / "digestions" aliases so `python -m llm_agents digest …`
    # (the __main__ dispatch words) also parse; args.cmd carries the alias.
    p_run = sub.add_parser(
        "run", aliases=("digest", "digestions"),
        help="digest in-scope articles missing a digestion row")
    p_run.add_argument("--limit", type=int, default=100,
                       help="Max articles this run; 0 = no limit "
                            "(default 100).")
    p_run.add_argument("--start-date", type=str, default=None,
                       help="Only articles dated on/after YYYY-MM-DD.")
    p_run.add_argument("--end-date", type=str, default=None,
                       help="Only articles dated on/before YYYY-MM-DD.")
    p_run.add_argument("--industry", type=str, default=None,
                       help="Only this industry_id (BANKS, SEMI, …).")
    p_run.add_argument("--source", type=str, default=None,
                       help="Only this text.news source (gov, zhihu, …).")
    p_run.add_argument("--force", action="store_true",
                       help="Re-digest every in-scope article, overwriting "
                            "existing rows (default: only missing ones).")
    p_run.add_argument("--include-empty", action="store_true",
                       help="Also digest NULL/empty-content rows "
                            "(title-only placeholders; default skipped).")
    p_run.add_argument("--provider", default="zhipu",
                       choices=sorted(PROVIDERS),
                       help="Chat provider (default zhipu).")
    p_run.add_argument("--model", type=str, default=None,
                       help="Chat model (default: provider default).")
    p_run.add_argument("--concurrency", type=int, default=4,
                       help="Parallel LLM calls (default 4).")
    p_run.add_argument("--max-content-chars", type=int, default=8000,
                       help="Article body truncation for the prompt "
                            "(default 8000).")
    p_run.add_argument("--lang", type=str, default="zh",
                       help="Summary language (default zh).")
    p_run.add_argument("--dry-run", action="store_true",
                       help="List the work queue without calling the LLM.")

    p_list = sub.add_parser("list", help="list digestion rows, newest first")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--industry", type=str, default=None)

    args = ap.parse_args()
    if args.cmd in ("run", "digest", "digestions"):
        await _run(args)
    elif args.cmd == "list":
        await _list(args)
