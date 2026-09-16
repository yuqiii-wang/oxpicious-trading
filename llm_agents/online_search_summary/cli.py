"""llm_agents.online_search_summary.cli — argparse CLI.

Usage::

    python -m llm_agents.online_search_summary search --query "…" \
        [--provider zhipu] [--engine search_pro] [--count 10] \
        [--recency oneWeek] [--domain www.sohu.com] [--content-size high] \
        [--json]
    python -m llm_agents.online_search_summary summarize --query "…" \
        [--mode native|compose] [--model glm-4-air] [--store] \
        [--industry BANKS] [--category macro] [--language zh] \
        [+ the search flags above] [--json]

``python -m llm_agents search|summarize …`` dispatches here too (see
llm_agents.__main__). ``--json`` prints one ``@@ONLINE_SEARCH_JSON@@``
marker line with the machine-readable envelope after the human logs — the
same convention as the zhihu downloader (its logger also writes stdout).
"""
from __future__ import annotations

# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()

from _common.build_commons import setup_utf8_stdout

setup_utf8_stdout()

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from typing import Optional  # noqa: E402

from _common.log_setup import setup_logging  # noqa: E402

from llm_agents.online_search_summary.core.models import (  # noqa: E402
    CONTENT_SIZES, RECENCY_FILTERS, SearchOptions,
)
from llm_agents.online_search_summary.providers.registry import (  # noqa: E402
    PROVIDERS, get_provider,
)
from llm_agents.online_search_summary.storage.store import (  # noqa: E402
    OnlineSearchSummaryStore,
)

logger = setup_logging("online_search_summary")

# stdout marker prefixing the JSON result envelope (zhihu-downloader
# convention: the logger stream also writes stdout, so API callers scan for
# the LAST marker line).
RESULT_MARKER = "@@ONLINE_SEARCH_JSON@@"


async def _run_tables_check(conn) -> None:
    for table in ("text.news", "text.llm_qa", "text.news_groups",
                  "text.news_group_items", "text.llm_qa_refs"):
        schema, name = table.split(".", 1)
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_schema = $1 AND table_name = $2)", schema, name)
        if not exists:
            logger.error("    [FATAL] missing table %s — run "
                         "database/sql/text/*.sql first", table)
            sys.exit(1)


def _print_json(envelope: dict) -> None:
    print(RESULT_MARKER + json.dumps(envelope, ensure_ascii=False))


def _add_search_args(p) -> None:
    p.add_argument("--query", required=True,
                   help="Question / search text (recommended <= 70 chars).")
    p.add_argument("--provider", default="zhipu", choices=sorted(PROVIDERS))
    p.add_argument("--engine", default=None,
                   help="Search engine code (default: provider default).")
    p.add_argument("--count", type=int, default=10,
                   help="Results to fetch, 1-50 (default 10).")
    p.add_argument("--recency", default="noLimit",
                   choices=list(RECENCY_FILTERS),
                   help="Publish-date window filter (default noLimit).")
    p.add_argument("--domain", default=None,
                   help="Restrict results to one site (e.g. www.sohu.com).")
    p.add_argument("--content-size", default="medium",
                   choices=list(CONTENT_SIZES),
                   help="Snippet length (default medium).")
    p.add_argument("--no-intent", action="store_true",
                   help="Skip intent recognition in standalone search.")
    p.add_argument("--json", action="store_true",
                   help="Print a machine-readable @@ONLINE_SEARCH_JSON@@ line.")


async def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m llm_agents.online_search_summary",
        description="Online search + LLM summary agent (providers: "
                    f"{sorted(PROVIDERS)}); optionally persists references "
                    "to text.news and the Q&A to text.llm_qa.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser(
        "search", help="standalone web search — list normalized references")
    _add_search_args(p_search)

    p_sum = sub.add_parser(
        "summarize", help="search + LLM summary; --store persists to text.*")
    _add_search_args(p_sum)
    p_sum.add_argument("--mode", default="native", choices=["native", "compose"],
                       help="native = provider one-shot search-in-chat; "
                            "compose = search then summarize (default native).")
    p_sum.add_argument("--model", default=None,
                       help="Chat model for the summary (provider default).")
    p_sum.add_argument("--store", action="store_true",
                       help="Persist references + Q&A into the text schema.")
    p_sum.add_argument("--no-resolve", action="store_true",
                       help="Skip per-ref resolution (no corpus match / "
                            "ddgs + markitdown): refs stored as snippet rows "
                            "typed relevant (fast, offline).")
    p_sum.add_argument("--industry", default=None,
                       help="Pin industry_id on stored rows (BANKS, SEMI, …).")
    p_sum.add_argument("--category", default=None,
                       help="text.llm_qa.category value.")
    p_sum.add_argument("--lang", default="zh",
                       help="Summary answer language, default zh (Chinese); "
                            "e.g. --lang en for English. Also stored as "
                            "text.llm_qa.language.")

    args = ap.parse_args()
    opts = SearchOptions(
        engine=args.engine, count=args.count, recency=args.recency,
        domain=args.domain, content_size=args.content_size,
        intent=not args.no_intent)

    provider = get_provider(args.provider)()
    if args.cmd == "search":
        resp = await provider.search(args.query, opts)
        for h in resp.hits:
            logger.info("[%s] %s — %s · %s\n    %s\n    %s", h.refer,
                        h.title, h.media or "-",
                        h.publish_date_raw or "-", h.link or "-",
                        (h.content or "")[:100])
        logger.info("%d hit(s)", len(resp.hits))
        if args.json:
            _print_json(resp.to_dict())
        return

    summary = await provider.summarize(args.query, model=args.model,
                                       opts=opts, mode=args.mode,
                                       lang=args.lang)
    logger.info("ANSWER (%s/%s, cited %s):\n%s", summary.provider,
                summary.model, ", ".join(summary.cited_refs) or "none",
                summary.answer)
    logger.info("REFERENCES:")
    for h in summary.hits:
        logger.info("  [%s] %s — %s · %s", h.refer, h.title, h.media or "-",
                    h.publish_date_raw or "-")
    qa_id: Optional[int] = None
    resolved = []
    if args.store:
        from _common.build_commons import get_db_or_exit
        conn = await get_db_or_exit()
        try:
            await _run_tables_check(conn)
            store = OnlineSearchSummaryStore(conn)
            qa_id, resolved = await store.store_summary(
                summary, industry_id=args.industry, category=args.category,
                language=args.lang, resolve=not args.no_resolve)
        finally:
            from _common.db_commons import close_pools_async
            await close_pools_async()
    if args.json:
        envelope = summary.to_dict()
        envelope["stored_qa_id"] = qa_id
        envelope["resolved_refs"] = [r.to_dict() for r in resolved]
        _print_json(envelope)
