"""Download the daily AI market summary — online search + LLM answer.

The RECENCY flow of the two AI-QA flows sharing text.llm_qa: this
script asks ONLY about the live snapshot — the general summary anchors
on today / the latest biz date and the movers phase reads ONLY the
latest daily cross_stats date — so it never backfills. The HISTORY
flow is ``llm_agents.industry_qa_weekly`` (seasonal top + MA5-slope
episode asks over the trading calendar, categories ``industry`` /
``market``); this flow stores under category ``market_daily``.

Runs the ``llm_agents.online_search_summary`` daily request preset
(``llm_agents.online_search_summary.daily``): the query asks for a
general summary of the day's stock market and which sectors saw
significant rises and drops. By default the request triggers on TODAY
(Asia/Shanghai) when it is a trading day, else the LATEST biz date — a
weekend/holiday run summarizes the most recent session. An explicit
``--date`` that lands on a non-trading day is clamped back to the latest
biz date on or before it.

The answer (+ normalized references) is stored under ``temps/ai_daily/``
as ``ai_daily_<biz-date>.json`` — cached: an existing valid artifact for
the target biz date skips the run (and its API spend) unless ``--force``.
``--store`` additionally persists the Q&A + references into the text
schema (text.llm_qa / text.news / text.llm_qa_refs) via
``OnlineSearchSummaryStore``, the same layer the
``llm_agents.online_search_summary summarize --store`` CLI uses; the
stored row is dated ``text.llm_qa.qa_date`` = the target biz date.

Best run after the market close (a morning run summarizes a session that
has not finished yet).

Usage::

    python -m downloads.macro.ai_daily                      # today / latest biz date
    python -m downloads.macro.ai_daily --date 2026-09-11    # a specific biz date
    python -m downloads.macro.ai_daily --store              # also persist to text.*
    python -m downloads.macro.ai_daily --force              # re-run over a cached artifact
    python -m downloads.macro.ai_daily --no-resolve         # fast --store (skip ref resolution)
    python -m downloads.macro.ai_daily --no-movers          # general summary only

Two phases per run:

1. **General summary** — the broad-market daily question above, stored
   (with ``--store``) under the broad-market industry tag by default.
2. **Industry movers** (``movers.py``; skip with ``--no-movers``) — from
   the LATEST daily ``stats.cross_stats`` industry grain (benchmark
   930903 = 中证A股): the top-3 rises and top-3 drops by
   benchmark-offset change AT MOST, loaded progressively through the 51%
   separation gate (a rank loads only when it differs from the last
   loaded one by >51% — clustered moves tell one story, so all 3 are not
   necessarily loaded), minus every industry+side still inside the
   5-trading-day cooldown. The cooldown binds the industry movers ONLY —
   the broadmarket general summary asks every run regardless. Asks
   default to the 5d search window (``oneWeek``). When the latest
   cross_stats date itself is >= 5 business days old (weekends/holidays
   excluded), the phase makes NO industry requests. Each survivor gets
   its own search+summary ask, persisted under its own industry_id with
   ``qa_date`` = the plan date.

Credentials come from ``GLM_ONLINE_SEARCH_KEY`` in the project-root
``.env`` (resolved by the provider layer). The parsed result is echoed
on stdout as a marker-prefixed JSON envelope (``AI_DAILY_RESULT_MARKER``)
— same convention as the zhihu downloader: the logger stream also writes
stdout, so API callers scan for the LAST marker line.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Make the project root importable when this module is executed directly
# (``python -m downloads.macro.ai_daily``) as well as imported as a
# package. ``__file__`` is downloads/macro/ai_daily/__init__.py, so
# parents[3] is the project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from _common.build_commons import setup_utf8_stdout  # noqa: E402

setup_utf8_stdout()  # Chinese answers on the Windows cp936 console

from downloads._common import resolve_out_dir, setup_logger  # noqa: E402
from llm_agents.online_search_summary import (  # noqa: E402
    DAILY_CATEGORY, DAILY_COUNT, SHANGHAI_TZ, OnlineSearchSummaryStore,
    SearchSummary, daily_request, daily_target_or_clamp,
    default_daily_target, get_provider,
)
from downloads.macro.ai_daily import movers  # noqa: E402

OUTPUT_DIR_NAME = "ai_daily"
# A valid stored artifact (answer + references) is a few KB at minimum;
# anything smaller is a truncated write and gets re-fetched.
CACHED_MIN_BYTES = 500
# Stdout marker prefixing the JSON result envelope (zhihu-downloader
# convention: the logger stream also writes stdout, so API callers scan
# for the LAST marker line).
AI_DAILY_RESULT_MARKER = "@@AI_DAILY_JSON@@"

logger = setup_logger("ai_daily")


def artifact_path(out_dir: Path, target: datetime.date) -> Path:
    return out_dir / f"ai_daily_{target.strftime('%Y-%m-%d')}.json"


def is_cached(path: Path) -> bool:
    """True when *path* holds a previously stored artifact for the target."""
    if not path.exists() or not path.is_file():
        return False
    try:
        if path.stat().st_size < CACHED_MIN_BYTES:
            return False
        with path.open("r", encoding="utf-8") as f:
            obj = json.load(f)
    except (ValueError, OSError):
        return False
    return isinstance(obj, dict) and bool(obj.get("answer"))


async def run_daily_summary(
    target: datetime.date,
    *,
    provider_name: str = "zhipu",
    model: Optional[str] = None,
    mode: str = "native",
    lang: str = "zh",
    count: Optional[int] = None,
) -> SearchSummary:
    """Ask the daily market/sector question; return the SearchSummary.

    The request (query + anchor-aware search options) comes from the
    ``daily`` preset in llm_agents.online_search_summary — this wrapper
    only executes it against the configured provider.
    """
    query, opts = daily_request(
        target, count=count if count is not None else DAILY_COUNT)
    provider = get_provider(provider_name)()
    logger.info("daily request [%s] via %s: %s", target, provider_name, query)
    summary = await provider.summarize(query, model=model, opts=opts,
                                       mode=mode, lang=lang)
    logger.info("ANSWER (%s/%s, cited %s):\n%s", summary.provider,
                summary.model, ", ".join(summary.cited_refs) or "none",
                summary.answer)
    logger.info("REFERENCES:")
    for h in summary.hits:
        logger.info("  [%s] %s — %s · %s", h.refer, h.title, h.media or "-",
                    h.publish_date_raw or "-")
    return summary


async def store_daily_summary(
    summary: SearchSummary,
    target: datetime.date,
    *,
    category: str = DAILY_CATEGORY,
    industry_id: Optional[str] = None,
    language: str = "zh",
    resolve: bool = True,
) -> Optional[int]:
    """Persist the daily Q&A into the text schema; return the qa_id.

    Mirrors the ``summarize --store`` CLI path: table pre-check, then
    OnlineSearchSummaryStore with ``qa_date`` pinned to the target biz
    date (SH midnight) so the AI page dates the card by the session it
    summarizes. The general summary is FOR the broad market, so an
    unset *industry_id* defaults to the broad-market industry tag
    (000001's parent classification) instead of NULL.
    """
    from _common.build_commons import get_db_or_exit

    conn = await get_db_or_exit()
    try:
        if industry_id is None:
            industry_id = await movers.broad_industry_id(conn)
            if industry_id:
                logger.info("general summary industry_id -> broad market "
                            "tag %s", industry_id)
        for table in ("text.news", "text.llm_qa", "text.news_groups",
                      "text.news_group_items", "text.llm_qa_refs"):
            schema, name = table.split(".", 1)
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_schema = $1 AND table_name = $2)",
                schema, name)
            if not exists:
                logger.error("    [FATAL] missing table %s — run "
                             "database/sql/text/*.sql first", table)
                sys.exit(1)
        store = OnlineSearchSummaryStore(conn)
        qa_id, resolved = await store.store_summary(
            summary, industry_id=industry_id, category=category,
            language=language, resolve=resolve,
            qa_date=datetime.datetime.combine(
                target, datetime.time.min, tzinfo=SHANGHAI_TZ))
        logger.info("stored text.llm_qa qa_id=%s (%d ref resolutions)",
                    qa_id, len(resolved))
        return qa_id
    finally:
        from _common.db_commons import close_pools_async
        await close_pools_async()


async def run_movers_phase(
    out_dir: Path,
    *,
    benchmark: str = movers.MOVERS_BENCHMARK,
    provider_name: str = "zhipu",
    model: Optional[str] = None,
    lang: str = "zh",
    count: Optional[int] = None,
    store: bool = False,
    resolve: bool = True,
    category: str = DAILY_CATEGORY,
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """Plan + ask the day's industry movers; persist the movers artifact.

    Resolves the latest cross_stats plan date, honours the movers
    artifact cache (``ai_daily_<plan-date>_movers.json`` — with *store*
    only when every stored ask already carries a qa_id), then delegates
    to :func:`movers.run_movers` on one open pool. Returns the movers
    envelope (None-candidates when cross_stats has no data yet).
    """
    from _common.build_commons import get_db_or_exit

    conn = await get_db_or_exit()
    try:
        plan_date, _offsets = await movers.fetch_latest_offsets(
            conn, benchmark)
        if plan_date is None:
            logger.warning("movers: stats.cross_stats_dates is empty — "
                           "run `python -m builds.cross_stats` first")
            return None
        out_path = movers.movers_artifact_path(out_dir, plan_date)
        if not force and movers.movers_cached(out_path):
            with out_path.open("r", encoding="utf-8") as f:
                cached = json.load(f)
            asked = cached.get("asked") or []
            store_done = (not asked
                          or all(o.get("qa_id") for o in asked))
            if store_done or not store:
                logger.info("movers: cached artifact %s exists — skipping",
                            out_path.name)
                return cached
        return await movers.run_movers(
            conn, benchmark=benchmark, provider_name=provider_name,
            model=model, lang=lang, count=count, store=store,
            resolve=resolve, category=category)
    finally:
        from _common.db_commons import close_pools_async
        await close_pools_async()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m downloads.macro.ai_daily",
        description="Daily AI stock-market summary: online search + LLM "
                    "answer for today / the latest biz date (sectors up "
                    "vs down), stored under temps/ai_daily/.")
    parser.add_argument("--date", type=str, default=None,
                        help="Target biz date YYYY-MM-DD (default: today "
                             "when a trading day, else the latest biz "
                             "date). A non-trading date is clamped back "
                             "to the latest biz date on or before it.")
    parser.add_argument("--provider", default="zhipu",
                        help="Online-search provider (default zhipu).")
    parser.add_argument("--model", default=None,
                        help="Chat model for the summary (provider default).")
    parser.add_argument("--mode", default="native",
                        choices=["native", "compose"],
                        help="native = one-shot search-in-chat; compose = "
                             "search then summarize (default native).")
    parser.add_argument("--lang", default="zh",
                        help="Summary answer language (default zh).")
    parser.add_argument("--count", type=int, default=None,
                        help=f"References to fetch (default: the daily "
                             f"preset's {DAILY_COUNT}).")
    parser.add_argument("--store", action="store_true",
                        help="Also persist the Q&A + references into the "
                             "text schema (text.llm_qa / text.news).")
    parser.add_argument("--no-resolve", action="store_true",
                        help="With --store: skip per-ref resolution "
                             "(fast, offline).")
    parser.add_argument("--no-movers", action="store_true",
                        help="Skip the industry-movers phase (the top "
                             "rise/drop sector asks driven by the latest "
                             "daily cross_stats).")
    parser.add_argument("--movers-benchmark", default=movers.MOVERS_BENCHMARK,
                        help=f"Benchmark of the industry-grain offsets "
                             f"(default {movers.MOVERS_BENCHMARK} = 中证A股).")
    parser.add_argument("--force", action="store_true",
                        help="Re-run even when a cached artifact exists "
                             "for the target biz date.")
    parser.add_argument("--category", default=DAILY_CATEGORY,
                        help=f"text.llm_qa.category "
                             f"(default {DAILY_CATEGORY}).")
    parser.add_argument("--industry", default=None,
                        help="Pin industry_id on stored rows.")
    parser.add_argument("--out-root", type=str, default=None,
                        help="Output root dir. Default: "
                             "<project>/temps/ai_daily")
    args = parser.parse_args()

    # Resolve + clamp the target onto the trading calendar: an explicit
    # --date snaps back to the latest biz date on or before it; the
    # default trigger is today when a trading day, else that same latest
    # biz date.
    if args.date:
        try:
            requested = datetime.datetime.strptime(
                args.date, "%Y-%m-%d").date()
        except ValueError:
            parser.error("--date must be YYYY-MM-DD")
        target, clamped = daily_target_or_clamp(requested)
        if clamped:
            logger.info("--date %s is not a trading day -> using latest "
                        "biz date %s", requested, target)
    else:
        target = default_daily_target()
        if target != datetime.datetime.now().date():
            logger.info("today is not a trading day -> summarizing the "
                        "latest biz date %s", target)

    out_dir = resolve_out_dir(str(Path(__file__).resolve()),
                              OUTPUT_DIR_NAME, args.out_root)
    out_path = artifact_path(out_dir, target)
    cached: Optional[Dict[str, Any]] = None
    if not args.force and is_cached(out_path):
        with out_path.open("r", encoding="utf-8") as f:
            cached = json.load(f)
        # A cached artifact skips the API run — but with --store only when
        # it ALSO records a stored qa_id, otherwise this run's purpose
        # (persisting into text.*) would be silently skipped.
        if not args.store or cached.get("stored_qa_id"):
            logger.info("cached artifact %s exists — skipping (use "
                        "--force to re-run)", out_path.name)
            print(AI_DAILY_RESULT_MARKER + json.dumps(
                cached, ensure_ascii=False), flush=True)
            return

    summary = asyncio.run(run_daily_summary(
        target, provider_name=args.provider, model=args.model,
        mode=args.mode, lang=args.lang, count=args.count))

    qa_id: Optional[int] = None
    if args.store:
        qa_id = asyncio.run(store_daily_summary(
            summary, target, category=args.category,
            industry_id=args.industry, language=args.lang,
            resolve=not args.no_resolve))

    # Industry movers: which sectors saw significant rises/drops on the
    # latest daily cross_stats date (top-3 per side at most, 51%
    # separation gate, 5d cooldown) — each survivor gets its own
    # search+summary ask. See movers.py for the plan semantics.
    movers_env: Optional[Dict[str, Any]] = None
    if not args.no_movers:
        movers_env = asyncio.run(run_movers_phase(
            out_dir, benchmark=args.movers_benchmark,
            provider_name=args.provider, model=args.model, lang=args.lang,
            count=args.count, store=args.store,
            resolve=not args.no_resolve, category=args.category,
            force=args.force))
        if movers_env and movers_env.get("status") == "ok":
            movers_path = movers.movers_artifact_path(
                out_dir,
                datetime.date.fromisoformat(movers_env["plan_date"]))
            movers_path.parent.mkdir(parents=True, exist_ok=True)
            with movers_path.open("w", encoding="utf-8") as f:
                json.dump(movers_env, f, ensure_ascii=False, indent=2)
            logger.info("saved %s (%d asked, %d failed)", movers_path.name,
                        len(movers_env.get("asked", [])),
                        len(movers_env.get("failed", [])))

    envelope: Dict[str, Any] = summary.to_dict()
    envelope["target_date"] = target.strftime("%Y-%m-%d")
    envelope["stored_qa_id"] = qa_id
    envelope["out_file"] = str(out_path)
    envelope["fetched_at"] = datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    if movers_env is not None:
        envelope["movers"] = movers_env
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False, indent=2)
    logger.info("saved %s", out_path)

    print(AI_DAILY_RESULT_MARKER + json.dumps(
        envelope, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
