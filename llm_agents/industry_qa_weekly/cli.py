"""llm_agents.industry_qa_weekly.cli — argparse CLI.

Usage::

    python -m llm_agents.industry_qa_weekly run [--as-of YYYY-MM-DD] \
        [--benchmark 000300] [--period 120] [--weighting equal] [--top 5] \
        [--dedupe-window 60] [--provider zhipu] [--model glm-5.2] \
        [--mode native] [--lang zh] [--no-resolve] [--concurrency 3] \
        [--limit 0] [--dry-run]
    python -m llm_agents.industry_qa_weekly backfill --start 2020-01-01 \
        [--end YYYY-MM-DD] [--interval 5] [--newest-first] [--no-cut-off] \
        [+ the run flags]

``run`` is the weekly entry point (idempotent — same-direction re-asks
within the 60-trading-day window are skipped; a direction flip — rise
then drop, or drop then rise — is always asked). ``backfill`` replays
the weekly step over a historical range on the 5-trading-day grid;
``--newest-first`` walks backwards from the covered present into the
past and cuts off automatically when a stretch of asks finds no refs
matching the trend description. ``python -m llm_agents industry-qa …``
dispatches here too (see llm_agents.__main__).
"""
from __future__ import annotations

# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()

from _common.build_commons import setup_utf8_stdout

setup_utf8_stdout()

import argparse  # noqa: E402
import datetime  # noqa: E402
import sys  # noqa: E402

from _common.log_setup import setup_logging  # noqa: E402

from llm_agents.industry_qa_weekly.core.agent import (
    CATEGORY, PlanTooLargeError, run_backfill, run_week,
)  # noqa: E402
from llm_agents.industry_qa_weekly.signals import (  # noqa: E402
    DEFAULT_BENCHMARK, DEFAULT_PERIOD_DAYS, DEFAULT_TOP_N,
    DEFAULT_WEIGHTING,
)

logger = setup_logging("industry_qa_weekly")


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def _add_common_args(p) -> None:
    p.add_argument("--benchmark", default=DEFAULT_BENCHMARK,
                   help=f"Broad-market benchmark code "
                        f"(default {DEFAULT_BENCHMARK}).")
    p.add_argument("--period", type=int, default=DEFAULT_PERIOD_DAYS,
                   help=f"Season ranking period in trading days, as in "
                        f"the Market Trend view (default "
                        f"{DEFAULT_PERIOD_DAYS} ≈ half a year).")
    p.add_argument("--weighting", default=DEFAULT_WEIGHTING,
                   choices=["equal", "amt"],
                   help=f"Ranking method (default {DEFAULT_WEIGHTING}).")
    p.add_argument("--top", type=int, default=DEFAULT_TOP_N,
                   help=f"Top-N industries per side "
                        f"(default {DEFAULT_TOP_N}).")
    p.add_argument("--dedupe-window", type=int, default=60,
                   help="Skip an industry already asked about in the SAME "
                        "direction (rise after rise / drop after drop) "
                        "within this many RANKING DATES (trading days; "
                        "default 60). A direction flip never skips — the "
                        "reversal is asked immediately.")
    p.add_argument("--provider", default="zhipu",
                   help="Online-search provider (default zhipu).")
    p.add_argument("--model", default=None,
                   help="Chat model for the summary (provider default).")
    p.add_argument("--mode", default="native",
                   choices=["native", "compose"],
                   help="Summary strategy for current-month anchors "
                        "(default native).")
    p.add_argument("--lang", default="zh",
                   help="Answer language (default zh).")
    p.add_argument("--no-resolve", action="store_true",
                   help="Store refs as snippet rows without per-ref "
                        "resolution (fast, offline).")
    p.add_argument("--concurrency", type=int, default=3,
                   help="Parallel asks per step (default 3).")
    p.add_argument("--limit", type=int, default=0,
                   help="Cap asks per step, 0 = no cap (testing).")
    p.add_argument("--dry-run", action="store_true",
                   help="Select + dedupe only; ask nothing, store nothing.")
    p.add_argument("--market-only", action="store_true",
                   help="Ask ONLY about the broad index's (000001) own "
                        "large rises/drops — skip the industry candidates.")
    p.add_argument("--seasonal-only", action="store_true",
                   help="Logic 1 only: seasonal top-5 hypes/drains per "
                        "season anchor.")
    p.add_argument("--annual-only", action="store_true",
                   help="Logic 2 only: per-industry MA5-deviation "
                        "episodes, rate-limited by the annual std caps.")
    p.add_argument("--industry-id", action="append", default=None,
                   help="Restrict the annual logic to this industry_id "
                        "(repeatable; e.g. --industry-id COMMS).")
    p.add_argument("--force-many-requests", action="store_true",
                   help="Skip the plan-size stop: proceed even when the "
                        "collected plan exceeds 100 ask(s).")


async def _run_tables_check(conn) -> None:
    for table in ("analysis.industry_hypes_and_drains",
                  "analysis.industry_hypes_seasonal", "text.news",
                  "text.llm_qa", "text.news_groups",
                  "text.news_group_items", "text.llm_qa_refs"):
        schema, name = table.split(".", 1)
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_schema = $1 AND table_name = $2)", schema, name)
        if not exists:
            logger.error("    [FATAL] missing table %s — run "
                         "database/sql/analysis/08_industry_hypes_and_drains"
                         ".sql and database/sql/text/*.sql first", table)
            sys.exit(1)


async def main() -> None:
    # Accept both invocation styles: `python -m llm_agents.industry_qa_weekly
    # run …` and the `python -m llm_agents industry-qa run …` dispatch,
    # which forwards the command token.
    argv = sys.argv[1:]
    if argv and argv[0] in ("industry-qa", "industry_qa"):
        argv = argv[1:]
    ap = argparse.ArgumentParser(
        prog="python -m llm_agents.industry_qa_weekly",
        description="Weekly Q&A: why are the top hypes & drains industries "
                    "rising/dropping? (asks the online-search summarize "
                    f"agent per industry, stores into text.llm_qa with "
                    f"category={CATEGORY!r} and the data date added).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser(
        "run", help="one weekly step at the latest ranking date "
                    "(--as-of to replay a specific week)")
    _add_common_args(p_run)
    p_run.add_argument("--as-of", default=None,
                       help="As-of date (default today; the latest ranking "
                            "<= it is used).")

    p_bf = sub.add_parser(
        "backfill", help="replay the weekly step over a date range")
    _add_common_args(p_bf)
    p_bf.add_argument("--start", required=True,
                      help="Range start YYYY-MM-DD.")
    p_bf.add_argument("--end", default=None,
                      help="Range end YYYY-MM-DD (default today).")
    p_bf.add_argument("--interval", type=int, default=5,
                      help="Step size in RANKING DATES (trading days; "
                           "default 5 = one trading week).")
    p_bf.add_argument("--newest-first", action="store_true",
                      help="Walk from --end backwards to --start — the "
                           "mode for extending history into the past from "
                           "an already-covered present.")
    p_bf.add_argument("--no-cut-off", action="store_true",
                      help="Keep walking even when many asks find no refs "
                           "matching the trend description.")

    args = ap.parse_args(argv)

    from _common.build_commons import get_db_or_exit
    from _common.db_commons import close_pools_async

    conn = await get_db_or_exit()
    try:
        await _run_tables_check(conn)
        from llm_agents.industry_qa_weekly.core.agent import run_backfill, run_week
        kwargs = dict(
            benchmark_code=args.benchmark, period_days=args.period,
            weighting=args.weighting, top_n=args.top,
            dedupe_window=args.dedupe_window, provider_name=args.provider,
            model=args.model, mode=args.mode, lang=args.lang,
            resolve=not args.no_resolve, concurrency=args.concurrency,
            limit=args.limit, dry_run=args.dry_run,
            market_only=args.market_only,
            seasonal_only=args.seasonal_only,
            annual_only=args.annual_only,
            industry_ids=args.industry_id,
            force_many_requests=args.force_many_requests)
        if args.cmd == "run":
            as_of = _parse_date(args.as_of) if args.as_of else None
            outcomes = await run_week(conn, as_of=as_of, **kwargs)
        else:
            try:
                outcomes = await run_backfill(
                    conn, start=_parse_date(args.start),
                    end=(_parse_date(args.end) if args.end else None),
                    interval=args.interval, newest_first=args.newest_first,
                    cut_off=not args.no_cut_off, **kwargs)
            except PlanTooLargeError as e:
                logger.error("[backfill] STOP: %s", e)
                sys.exit(2)

        n_asked = sum(1 for c in outcomes if c.skipped is None
                      and c.error is None)
        n_skip = sum(1 for c in outcomes if c.skipped)
        n_err = sum(1 for c in outcomes if c.error)
        label = "would ask" if args.dry_run else "asked"
        logger.info("[done] %d candidate(s): %d %s, %d skipped, %d "
                    "failed", len(outcomes), n_asked, label, n_skip, n_err)
        for c in outcomes:
            tag = (c.skipped or c.error or f"qa_id={c.qa_id}")
            logger.info("  %s %s r%d %-12s %s | %s",
                        c.signal.date, c.signal.side, c.signal.rank,
                        c.signal.industry_label, tag, c.question)
    finally:
        await close_pools_async()
