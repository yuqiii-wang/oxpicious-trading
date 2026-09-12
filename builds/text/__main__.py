"""builds.text — CLI entry point: load downloaded news into the text schema.

Pipeline (all steps reuse one DB connection):

  1. Parse the downloaded news artifacts (builds.text.loaders) for the
     requested --sources.
  2. Restrict to the --start-date / --end-date window (default: all dates).
  3. Tag industries + extract keywords (builds.text.keywords — integrated
     sec_classification catalog + news keyword taxonomy).
  4. Upsert text.news, refresh text.news_keywords for new/changed articles,
     recompute doc_freq/idf corpus-wide, fill today_industry_change from
     stats.industry_basic_stats.

Incremental semantics: text.news upserts are idempotent; keyword rows are
rewritten only for articles whose stored word_count/industry_id changed
(downloaded files are immutable per (title, source, date)). --force rewrites
keyword rows for every article in the selected window.

The schema is owned by database/sql/text/*.sql — run those first on a fresh
database; this build exits with a hint when the tables are missing.

Usage::

    python -m builds.text                                   # all sources
    python -m builds.text --sources gov,zhihu               # subset
    python -m builds.text --start-date 2026-09-01           # date window
    python -m builds.text --force                           # refresh all keyword rows
    python -m builds.text --no-keywords                     # text.news only
"""
from __future__ import annotations

# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()

import warnings
warnings.filterwarnings("ignore")

from _common.build_commons import setup_utf8_stdout

setup_utf8_stdout()

import argparse
import asyncio
import datetime
import sys
from typing import List, Optional

from _common.build_commons import (
    bulk_upsert_async, get_db_or_exit, print_build_header,
)
from _common.log_setup import setup_logging  # noqa: E402

from builds.text import upsert
from builds.text.keywords import extract_for_articles
from builds.text.loaders import ALL_SOURCES, load_news, load_zhihu_comments

logger = setup_logging("text")

TODAY_STR = datetime.date.today().isoformat()


def _parse_date_arg(value: Optional[str], flag: str) -> Optional[datetime.date]:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        logger.error("    [FATAL] invalid %s (expected YYYY-MM-DD): %s",
                     flag, value)
        sys.exit(2)


async def _tables_exist(conn, tables: List[str]) -> List[str]:
    """Return the subset of schema-qualified *tables* missing from the DB."""
    missing = []
    for table in tables:
        schema, name = table.split(".", 1)
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_schema = $1 AND table_name = $2)", schema, name)
        if not exists:
            missing.append(table)
    return missing


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Load downloaded news text into text.news / text.news_keywords.")
    ap.add_argument("--sources", type=str, default=None,
                    help=f"Comma-separated source subset. Default: all of "
                         f"{','.join(ALL_SOURCES)}")
    ap.add_argument("--start-date", type=str, default=None,
                    help="Only load articles dated on/after YYYY-MM-DD.")
    ap.add_argument("--end-date", type=str, default=None,
                    help="Only load articles dated on/before YYYY-MM-DD.")
    ap.add_argument("--force", action="store_true",
                    help="Rewrite keyword rows for every article (default: only "
                         "new/changed articles).")
    ap.add_argument("--no-keywords", action="store_true",
                    help="Skip text.news_keywords (text.news only).")
    ap.add_argument("--no-industry-change", action="store_true",
                    help="Skip the today_industry_change update.")
    args = ap.parse_args()

    sources = ([s.strip() for s in args.sources.split(",") if s.strip()]
               if args.sources else None)
    start = _parse_date_arg(args.start_date, "--start-date")
    end = _parse_date_arg(args.end_date, "--end-date")

    t0 = datetime.datetime.now()
    print_build_header(
        "BUILD TEXT  ·  downloaded news -> text.news",
        **{"Sources": ",".join(sources or ALL_SOURCES),
           "Window": f"{start or '…'} → {end or '…'}",
           "Mode": "force (refresh keyword rows)" if args.force else "incremental",
           "Today": TODAY_STR,
           })

    # --- 1. Parse downloaded artifacts --------------------------------------
    logger.info("\n[1/4] Parsing downloaded news …")
    rows = load_news(sources)
    if start:
        rows = [r for r in rows if r["date"] >= start]
    if end:
        rows = [r for r in rows if r["date"] <= end]
    if not rows:
        logger.info("    no rows in scope — nothing to do")
        return
    logger.info("    %d articles in scope (%s → %s)",
                len(rows), min(r["date"] for r in rows),
                max(r["date"] for r in rows))

    # --- 2. Industry tags + keyword extraction ------------------------------
    if not args.no_keywords:
        logger.info("\n[2/4] Tagging industries + extracting keywords …")
        extract_for_articles(rows)
        tagged = sum(1 for r in rows if r.get("industry_id"))
        kw_total = sum(len(r.get("keywords", {})) for r in rows)
        logger.info("    %d/%d articles industry-tagged, %d keyword rows",
                    tagged, len(rows), kw_total)
    else:
        logger.info("\n[2/4] Keyword extraction skipped (--no-keywords)")

    # --- 3. Connect + upsert text.news --------------------------------------
    logger.info("\n[3/4] Connecting to database …")
    conn = await get_db_or_exit()
    try:
        missing = await _tables_exist(
            conn, ["text.news", "text.news_keywords"])
        if missing:
            logger.error("    [FATAL] missing tables %s — run "
                         "database/sql/text/*.sql first", missing)
            sys.exit(1)

        # Pre-upsert state decides which articles need keyword work.
        needing = upsert.select_articles_needing_keywords(
            rows, await upsert.fetch_existing_news(conn), force=args.force)
        logger.info("    [DB] upserting %d rows into text.news "
                    "(%d new/changed) …", len(rows), len(needing))
        await bulk_upsert_async(conn, upsert.NEWS_TABLE,
                                upsert.build_news_rows(rows),
                                ["title", "source", "date"])
        post_existing = await upsert.fetch_existing_news(conn)
        articles = upsert.map_news_ids(needing, post_existing)

        # --- text.news_comments (zhihu only for now) -------------------------
        # Loader rows are PK-deduped in-batch; fetch_existing_comment_pks +
        # filter_new_comments drop already-stored PKs before the write, so a
        # re-run is idempotent and the upsert never conflicts twice.
        if sources is None or "zhihu" in sources:
            comment_rows = load_zhihu_comments()
            news_id_by_key = {
                key: entry["news_id"] for key, entry in post_existing.items()}
            comments = upsert.build_comment_rows(comment_rows, news_id_by_key)
            existing_pks = await upsert.fetch_existing_comment_pks(conn)
            new_comments = upsert.filter_new_comments(comments, existing_pks)
            logger.info("    [DB] comments: %d parsed, %d resolved to news, "
                        "%d new", len(comment_rows), len(comments), len(new_comments))
            await upsert.insert_comments(conn, new_comments)

        # --- 4. text.news_keywords ------------------------------------------
        if args.no_keywords:
            logger.info("\n[4/4] text.news_keywords skipped (--no-keywords)")
            return
        logger.info("\n[4/4] Writing text.news_keywords …")
        await upsert.refresh_keyword_rows(conn, articles)
        await upsert.recompute_keyword_stats(conn)
        if not args.no_industry_change:
            n = await upsert.apply_today_industry_change(conn, articles)
            logger.info("    [DB] today_industry_change set for %d "
                        "(industry, date) pairs", n)
    finally:
        from _common.db_commons import close_pools_async
        await close_pools_async()

    print_build_header("BUILD TEXT DONE", **{"Wall time": f"{(datetime.datetime.now() - t0).total_seconds():.1f}s"})


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
