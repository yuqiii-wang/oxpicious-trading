"""Download the daily AI market summary — online search + LLM answer.

The LIVE AI-QA flow writing llm.llm_qa (this script asks ONLY about
the live snapshot — the general summary anchors on today / the latest
biz date and the movers phase reads ONLY the latest daily cross_stats
date — so it never backfills). Its artifacts are loaded into the
knowledge base by ``builds.text`` under category ``market_daily``; the
legacy weekly history backfill (seasonal top + MA5-slope episode asks,
categories ``industry`` / ``market``) has been removed, and its rows
remain queryable in llm.llm_qa.

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
Like every other downloader, this module NEVER touches the database:
``builds.text`` (source ``ai_daily``) loads the artifacts into the text
schema — answers + reference hits into text.news, the Q&A into
llm.llm_qa (+ llm.llm_qa_refs / text.news_groups provenance) under
category ``market_daily``, see builds.text.ai_daily_qa.

Best run after the market close (a morning run summarizes a session that
has not finished yet).

Usage::

    python -m downloads.macro.ai_daily                      # today / latest biz date
    python -m downloads.macro.ai_daily --date 2026-09-11    # a specific biz date
    python -m downloads.macro.ai_daily --force              # re-run over a cached artifact
    python -m downloads.macro.ai_daily --no-movers          # general summary only
    python -m downloads.macro.ai_daily --no-geo             # skip the geopolitics asks

Two phases per run:

1. **General summary** — the broad-market daily question above, loaded
   by builds.text under the broad-market industry tag by default.
2. **Daily geopolitics/economy asks** (skip with ``--no-geo``) — the
   two questions besides the broad-market summary, from the ``daily``
   preset's ``daily_geo_asks``: the day's major GLOBAL
   geopolitics/politics/economy news and its financial-market
   implications (asked and answered in ENGLISH) and the same for CHINA
   (asked and answered in CHINESE). Broad-market asks like the general
   summary — no gate, no cooldown, every run; loaded by builds.text
   under the broad-market tag with per-ask language stored on
   llm.llm_qa.
3. **Industry movers** (``movers.py``; skip with ``--no-movers``) — from
   the LATEST daily ``stats.cross_stats`` industry grain (benchmark
   930903 = 中证A股): the top-3 rises and top-3 drops by
   benchmark-offset change AT MOST, loaded progressively through the
   51% separation gate (a rank loads only when it differs from the last
   loaded one by >51% — clustered moves tell one story, so all 3 are not
   necessarily loaded), minus every industry+side still inside the
   5-trading-day cooldown. The movers phase is the WEEKLY industry
   step: it fires at most once per 5 trading days (an industry ask
   inside the last 5-trading-day window short-circuits the phase,
   status ``weekly-skip``) — DAILY asks are broadmarket (the general
   summary), WEEKLY asks are industry. The cooldown binds the industry
   movers ONLY — the broadmarket general summary asks every run
   regardless. Asks default to the 5d search window (``oneWeek``).
   When the latest cross_stats date itself is >= 5 business days old
   (weekends/holidays excluded), the phase makes NO industry requests.
   Each survivor gets its own search+summary ask, loaded by builds.text
   under its own industry_id with ``qa_date`` = the plan date.

Credentials come from ``GLM_ONLINE_SEARCH_KEY`` in the project-root
``.env`` (resolved by the provider layer). The parsed result is echoed
on stdout as a marker-prefixed JSON envelope (``AI_DAILY_RESULT_MARKER``)
— same convention as the zhihu downloader: the logger stream also writes
stdout, so API callers scan for the LAST marker line.

The entry point is the :class:`AiDailyDownload` DownloadJob — the former
``main()`` body is its ``run()`` (argparse moved to ``add_arguments`` /
``apply_args``) and the three async phase functions became methods; the
artifact-path / cache-check helpers stay module-level.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make the project root importable when this module is executed directly
# (``python -m downloads.macro.ai_daily``) as well as imported as a
# package. ``__file__`` is downloads/macro/ai_daily/__init__.py, so
# parents[3] is the project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from _common.build_commons import setup_utf8_stdout  # noqa: E402

setup_utf8_stdout()  # Chinese answers on the Windows cp936 console

from downloads._common import DownloadJob, resolve_out_dir, setup_logger  # noqa: E402
from llm_agents.online_search_summary import (  # noqa: E402
    DAILY_COUNT, SearchSummary, daily_geo_asks, daily_request,
    daily_target_or_clamp, default_daily_target, get_provider,
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


class AiDailyDownload(DownloadJob):
    """DownloadJob for the daily AI market summary — the former ``main()``
    body is ``run()`` verbatim (the target-date resolution moved to
    ``apply_args``), the argparse surface moved to ``add_arguments`` and
    the three async phase functions (general summary / geo asks / movers
    phase) became methods with unchanged bodies."""

    description = ("Daily AI stock-market summary: online search + LLM "
                   "answer for today / the latest biz date (sectors up "
                   "vs down), stored under temps/ai_daily/.")
    component = "ai_daily"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = super().build_parser()
        parser.prog = "python -m downloads.macro.ai_daily"
        return parser

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--date", type=str, default=None,
                            help="Target biz date YYYY-MM-DD (default: today "
                                 "when a trading day, else the latest biz "
                                 "date). A non-trading date is clamped back "
                                 "to the latest biz date on or before it.")
        parser.add_argument("--provider", default="zhipu",
                            help="Online-search provider (default zhipu).")
        parser.add_argument("--model", default=None,
                            help="Chat model for the summary (provider default).")
        parser.add_argument("--mode", default="compose",
                            choices=["native", "compose"],
                            help="native = one-shot search-in-chat; compose = "
                                 "search then summarize (default compose — the "
                                 "separated two-step).")
        parser.add_argument("--lang", default="zh",
                            help="Summary answer language (default zh).")
        parser.add_argument("--count", type=int, default=None,
                            help=f"References to fetch (default: the daily "
                                 f"preset's {DAILY_COUNT}).")
        parser.add_argument("--no-movers", action="store_true",
                            help="Skip the industry-movers phase (the top "
                                 "rise/drop sector asks driven by the latest "
                                 "daily cross_stats).")
        parser.add_argument("--no-geo", action="store_true",
                            help="Skip the daily geopolitics/economy asks (the "
                                 "global-English and China-Chinese news + "
                                 "market-implications questions).")
        parser.add_argument("--movers-benchmark", default=movers.MOVERS_BENCHMARK,
                            help=f"Benchmark of the industry-grain offsets "
                                 f"(default {movers.MOVERS_BENCHMARK} = 中证A股).")
        parser.add_argument("--no-research", action="store_true",
                            help="Skip the AI Research auto-run embedded in the "
                                 "movers phase (per-industry research-logic Q&A "
                                 "generation, quarterly cooldown by default).")
        parser.add_argument("--force", action="store_true",
                            help="Re-run even when a cached artifact exists "
                                 "for the target biz date.")
        parser.add_argument("--out-root", type=str, default=None,
                            help="Output root dir. Default: "
                                 "<project>/temps/ai_daily")

    def apply_args(self) -> None:
        # Resolve + clamp the target onto the trading calendar: an explicit
        # --date snaps back to the latest biz date on or before it; the
        # default trigger is today when a trading day, else that same latest
        # biz date.
        if self.args.date:
            try:
                requested = datetime.datetime.strptime(
                    self.args.date, "%Y-%m-%d").date()
            except ValueError:
                self.parser.error("--date must be YYYY-MM-DD")
            target, clamped = daily_target_or_clamp(requested)
            if clamped:
                logger.info("--date %s is not a trading day -> using latest "
                            "biz date %s", requested, target)
        else:
            target = default_daily_target()
            if target != datetime.datetime.now().date():
                logger.info("today is not a trading day -> summarizing the "
                            "latest biz date %s", target)
        self.args.target = target

    async def run_daily_summary(
        self,
        target: datetime.date,
        *,
        provider_name: str = "zhipu",
        model: Optional[str] = None,
        mode: str = "compose",
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

    async def run_geo_asks(
        self,
        target: datetime.date,
        *,
        provider_name: str = "zhipu",
        model: Optional[str] = None,
        count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Ask the two daily geopolitics/economy questions; return the envelope.

        One search+summary ask per :class:`GeoAsk` of the ``daily`` preset's
        ``daily_geo_asks`` — GLOBAL news asked and answered in English,
        CHINA news in Chinese, each asking for the day's news and its
        financial-market implications. A failed ask never kills the pair —
        its outcome lands in ``failed`` with the error. The envelope embeds
        in the general artifact under ``geo``; builds.text loads each ask's
        Q&A into llm.llm_qa (broad-market tag, per-ask language) and the
        references into text.news.
        """
        provider = get_provider(provider_name)()
        asks = daily_geo_asks(
            target, count=count if count is not None else DAILY_COUNT)
        asks_out: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        for ask in asks:
            logger.info("geo request [%s/%s] via %s: %s", ask.scope, ask.lang,
                        provider_name, ask.query)
            try:
                summary = await provider.summarize(
                    ask.query, model=model, opts=ask.opts, lang=ask.lang)
                asks_out.append({
                    "scope": ask.scope, "lang": ask.lang,
                    "question": ask.query, "answer": summary.answer,
                    "cited_refs": summary.cited_refs,
                    "n_refs": len(summary.hits),
                    # The ask's FULL hit list — builds.text resolves these into
                    # text.news rows + llm.llm_qa provenance (self-sufficient
                    # artifact, same contract as the movers asks).
                    "references": [h.to_dict() for h in summary.hits],
                    "provider": summary.provider, "model": summary.model,
                })
                logger.info("ANSWER (%s/%s, cited %s):\n%s", summary.provider,
                            summary.model, ", ".join(summary.cited_refs) or "none",
                            summary.answer)
                logger.info("REFERENCES:")
                for h in summary.hits:
                    logger.info("  [%s] %s — %s · %s", h.refer, h.title,
                                h.media or "-", h.publish_date_raw or "-")
            except Exception as e:  # one failed ask must not kill the pair
                failed.append({"scope": ask.scope, "lang": ask.lang,
                               "question": ask.query,
                               "error": f"{type(e).__name__}: {e}"})
                logger.warning("geo [%s] %s -> FAILED %s: %s", ask.scope,
                               ask.query, type(e).__name__, e)
        return {"status": "ok", "target_date": target.strftime("%Y-%m-%d"),
                "asks": asks_out, "failed": failed}

    async def run_movers_phase(
        self,
        out_dir: Path,
        *,
        benchmark: str = movers.MOVERS_BENCHMARK,
        provider_name: str = "zhipu",
        model: Optional[str] = None,
        lang: str = "zh",
        count: Optional[int] = None,
        force: bool = False,
        no_research: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Plan + ask the day's industry movers; persist the movers artifact.

        Resolves the latest cross_stats plan date, honours the movers
        artifact cache (``ai_daily_<plan-date>_movers.json``), then delegates
        to :meth:`movers.Movers.run_movers` on one open pool. Returns the
        movers envelope (None-candidates when cross_stats has no data yet).

        On an OK run the AI Research auto-run is EMBEDDED here: every
        industry the plan loaded gets one ``generate_research_qa`` pass
        (quarterly cooldown by default; ``force`` overrides; ``no_research``
        skips entirely) — the envelope carries the outcomes under
        ``research``.
        """
        from _common.build_commons import get_db_or_exit

        conn = await get_db_or_exit()
        try:
            mv = movers.Movers()
            plan_date, _offsets = await mv.fetch_latest_offsets(
                conn, benchmark)
            if plan_date is None:
                logger.warning("movers: stats.cross_stats_dates is empty — "
                               "run `python -m builds.cross_stats` first")
                return None
            out_path = movers.movers_artifact_path(out_dir, plan_date)
            if not force and movers.movers_cached(out_path):
                with out_path.open("r", encoding="utf-8") as f:
                    cached = json.load(f)
                logger.info("movers: cached artifact %s exists — skipping",
                            out_path.name)
                return cached
            env = await mv.run_movers(
                conn, benchmark=benchmark, provider_name=provider_name,
                model=model, lang=lang, count=count)
            if env.get("status") == "ok" and not no_research:
                from .research_autorun import ResearchAutorun
                candidates = [p for side in ("rises", "drops")
                              for p in env.get("candidates", {}).get(side, [])]
                if candidates:
                    env["research"] = await ResearchAutorun().run(
                        conn, candidates, provider_name=provider_name,
                        model=model, lang=lang, force=force)
            return env
        finally:
            from _common.db_commons import close_pools_async
            await close_pools_async()

    def run(self) -> None:
        """One download cycle — the former ``main()`` body (argparse parsing
        and the target-date resolution are owned by the base template
        methods / ``apply_args``)."""
        out_dir = resolve_out_dir(str(Path(__file__).resolve()),
                                  OUTPUT_DIR_NAME, self.args.out_root)
        out_path = artifact_path(out_dir, self.args.target)
        if not self.args.force and is_cached(out_path):
            logger.info("cached artifact %s exists — skipping (use --force to "
                        "re-run)", out_path.name)
            with out_path.open("r", encoding="utf-8") as f:
                cached = json.load(f)
            print(AI_DAILY_RESULT_MARKER + json.dumps(
                cached, ensure_ascii=False), flush=True)
            return

        summary = asyncio.run(self.run_daily_summary(
            self.args.target, provider_name=self.args.provider,
            model=self.args.model, mode=self.args.mode,
            lang=self.args.lang, count=self.args.count))

        # Daily geopolitics/economy asks: the two questions besides the
        # broad-market summary — global news in English, China news in
        # Chinese. Broad-market asks like the summary itself: no gate, no
        # cooldown, every run.
        geo_env: Optional[Dict[str, Any]] = None
        if not self.args.no_geo:
            geo_env = asyncio.run(self.run_geo_asks(
                self.args.target, provider_name=self.args.provider,
                model=self.args.model, count=self.args.count))

        # Industry movers: which sectors saw significant rises/drops on the
        # latest daily cross_stats date (top-3 per side at most, 51%
        # separation gate, 5d cooldown) — each survivor gets its own
        # search+summary ask. See movers.py for the plan semantics.
        movers_env: Optional[Dict[str, Any]] = None
        if not self.args.no_movers:
            movers_env = asyncio.run(self.run_movers_phase(
                out_dir, benchmark=self.args.movers_benchmark,
                provider_name=self.args.provider, model=self.args.model,
                lang=self.args.lang, count=self.args.count,
                force=self.args.force, no_research=self.args.no_research))
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
        envelope["target_date"] = self.args.target.strftime("%Y-%m-%d")
        envelope["out_file"] = str(out_path)
        envelope["fetched_at"] = datetime.datetime.now(
            datetime.timezone.utc).isoformat()
        if movers_env is not None:
            envelope["movers"] = movers_env
        if geo_env is not None:
            envelope["geo"] = geo_env
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(envelope, f, ensure_ascii=False, indent=2)
        logger.info("saved %s", out_path)

        print(AI_DAILY_RESULT_MARKER + json.dumps(
            envelope, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    AiDailyDownload().execute()
