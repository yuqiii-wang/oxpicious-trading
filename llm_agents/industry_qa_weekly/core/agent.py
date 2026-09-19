"""llm_agents.industry_qa_weekly.agent — sudden-move Q&A agent.

Candidates:

* INDUSTRY — sudden MA5 rises/drops vs the benchmark (000300): an
  industry's member-index mean close has a 5-day average (MA5) whose
  day-over-day slope deviates from the benchmark's own MA5 slope by more
  than IND_MA5_DEV_PCT points. Consecutive trigger days merge into one
  EPISODE; the ask is anchored at the NEXT trading day after the episode
  ends, and rate-limited per industry per calendar year by the annual
  std of that deviation series (low-vol <= 4 asks, high-vol <= 12).
* MARKET — the broad index (上证指数 000001) itself rising/dropping
  sharply (|daily| >= 3% or trailing week >= 6%), same episode +1-day
  anchoring, tagged with the index's PARENT industry/sector tags
  (BROAD_SSE / BROAD).

Backfills are PLAN-FIRST: the complete ask plan is computed and logged
before any request, planning memory is released, a plan larger than
MAX_PLAN_ASKS raises PlanTooLargeError (skip: --force-many-requests),
and execution is strictly sequential request -> DB store.
"""
from __future__ import annotations

import asyncio
import datetime
import gc
import logging
import statistics
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from llm_agents.industry_qa_weekly.questions import (
    asked_within, build_market_question, build_question, in_period,
    market_question_base, market_search_query, market_widen_query,
    merge_hits, period_search_query, period_widen_query, prefer_dated,
    question_base, recency_for_anchor, MARKET_INDEX_LABEL,
)
from llm_agents.industry_qa_weekly.signals import (
    DEFAULT_BENCHMARK, DEFAULT_PERIOD_DAYS, DEFAULT_TOP_N,
    DEFAULT_WEIGHTING, IndustrySignal, fetch_index_parent_tags,
    iter_industry_episodes, fetch_market_episodes, fetch_market_triggers,
    fetch_ranking_dates, fetch_top_industries, IND_ANNUAL_HIGH_CAP,
    IND_ANNUAL_LOW_CAP, IND_ANNUAL_LOW_STD, MARKET_ANNUAL_CAP,
    MARKET_DEDUPE_WINDOW, MARKET_LOOKBACK_RANKING_DATES,
)

logger = logging.getLogger(__name__)

# text.llm_qa.category for script-generated industry questions.
CATEGORY = "industry"

DEFAULT_INTERVAL = 5          # kept for CLI compatibility (plan is episode-based)
# Same-direction re-ask skip window (trading dates) for industry episodes.
DEFAULT_DEDUPE_WINDOW = 60

# Plan-first guard: a collected plan larger than this stops the backfill
# before any request is made (skip with --force-many-requests).
# AND combine: when both industry logics run, an annual MA5-deviation
# episode only asks when the seasonal top-N ranking picked the same
# industry+side at an anchor within this many calendar days of the
# episode's ask date.
SEASONAL_ANNUAL_MATCH_DAYS = 15
MAX_PLAN_ASKS = 100


class PlanTooLargeError(RuntimeError):
    """The collected ask plan exceeds MAX_PLAN_ASKS. Skip with
    --force-many-requests when the spend is understood and wanted."""

    def __init__(self, count: int, cap: int = MAX_PLAN_ASKS):
        self.count, self.cap = count, cap
        super().__init__(
            f"plan has {count} ask(s) > cap {cap} — pass "
            f"--force-many-requests to proceed anyway")


def _rss_mib() -> float:
    """Resident set size in MiB (Linux /proc), -1 when unavailable."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return -1.0


@dataclass
class AskOutcome:
    """What happened to one planned ask."""
    signal: IndustrySignal
    question: str
    kind: str = "industry"           # "industry" | "market"
    skipped: Optional[str] = None    # None when asked; else the reason
    qa_id: Optional[int] = None
    model: Optional[str] = None
    n_refs: int = 0                  # references the summary cited/carried
    n_stored_refs: int = 0           # refs resolved into text.llm_qa_refs
    error: Optional[str] = None


async def ask_one(
    conn, signal: IndustrySignal, question: str, *,
    provider_name: str = "zhipu",
    model: Optional[str] = None,
    lang: str = "zh",
    resolve: bool = True,
    kind: str = "industry",
) -> AskOutcome:
    """Ask + store ONE question; returns the AskOutcome.

    Historical asks (anchor older than ~a month) use period-scoped
    compose retrieval: search a leading year-month query, drop every hit
    published after the anchor, widen once when too few survive, and
    summarize over exactly those hits. Recent anchors use the provider's
    native search-in-chat with a recency window instead."""
    from llm_agents.online_search_summary import (
        OnlineSearchSummaryStore, SearchOptions, SHANGHAI_TZ, get_provider,
    )

    outcome = AskOutcome(signal=signal, question=question, kind=kind)
    try:
        provider = get_provider(provider_name)()
        recency = recency_for_anchor(signal.date)
        is_market = kind == "market"
        if recency == "noLimit":
            if is_market:
                primary = market_search_query(signal.side, signal.date,
                                              signal.industry_label
                                              or MARKET_INDEX_LABEL)
                widen = market_widen_query(signal.date,
                                           signal.industry_label
                                           or MARKET_INDEX_LABEL)
            else:
                primary = period_search_query(signal.industry_label,
                                              signal.side, signal.date)
                widen = period_widen_query(signal.industry_label,
                                           signal.date)
            resp = await provider.search(primary,
                                         SearchOptions(recency="noLimit"))
            hits = in_period(resp.hits, signal.date)
            if len(hits) < 2:  # widen once with the bare year-month query
                resp2 = await provider.search(widen,
                                              SearchOptions(recency="noLimit"))
                hits = merge_hits(hits, in_period(resp2.hits, signal.date))
            # An undated hit's stored ref_time falls back to now() — the
            # exact shape of the wrong-year refs — so undated hits are
            # kept only when there is NO dated in-period coverage.
            hits = prefer_dated(hits, min_dated=1)
            summary = await provider.summarize_hits_via_compose(
                question, hits, model=model, lang=lang)
        else:
            opts = SearchOptions(recency=recency)
            summary = await provider.summarize(
                question, model=model, opts=opts, lang=lang)
        outcome.model = summary.model
        outcome.n_refs = len(summary.hits)
        store = OnlineSearchSummaryStore(conn)
        # text.llm_qa.qa_date = the question's （截至…） anchor (SH
        # midnight) — the AI page's per-card date + event-strip mapping.
        qa_date = datetime.datetime.combine(
            signal.date, datetime.time.min, tzinfo=SHANGHAI_TZ)
        qa_id, resolved = await store.store_summary(
            summary, industry_id=signal.industry_id,
            category="market" if is_market else CATEGORY,
            language=lang, resolve=resolve, qa_date=qa_date)
        outcome.qa_id = qa_id
        outcome.n_stored_refs = len(resolved)
    except Exception as e:  # one failed ask must not kill the batch
        outcome.error = f"{type(e).__name__}: {e}"
        logger.warning("    [ask] %s -> FAILED %s", question, outcome.error)
    return outcome


async def run_backfill(
    conn, *,
    start: datetime.date,
    end: Optional[datetime.date] = None,
    interval: int = 5,
    dedupe_window: int = 60,
    benchmark_code: str = DEFAULT_BENCHMARK,
    period_days: int = DEFAULT_PERIOD_DAYS,
    weighting: str = DEFAULT_WEIGHTING,
    top_n: int = DEFAULT_TOP_N,
    provider_name: str = "zhipu",
    model: Optional[str] = None,
    mode: str = "native",
    lang: str = "zh",
    resolve: bool = True,
    concurrency: int = 3,
    limit: int = 0,
    dry_run: bool = False,
    newest_first: bool = False,
    cut_off: bool = True,
    market_only: bool = False,
    seasonal_only: bool = False,
    annual_only: bool = False,
    industry_ids: Optional[List[str]] = None,
    force_many_requests: bool = False,
) -> List[AskOutcome]:
    """PLAN-FIRST backfill over [start, end] on the index trading calendar.

    Two industry-selection logics feed the plan; run together (the
    default) they combine with AND — an ask must qualify under BOTH:

    * SEASONAL — what the "Hypes & Drains" view shows: the top-N HYPE +
      top-N DRAIN industries per season anchor.
    * ANNUAL — per-industry MA5-deviation episodes, rate-limited per
      industry per calendar year by the std of the deviation series
      (low-vol <= IND_ANNUAL_LOW_CAP asks, high-vol <=
      IND_ANNUAL_HIGH_CAP; strongest first).

    An annual episode passes the AND gate when the seasonal ranking
    picked the same industry+side at an anchor within
    SEASONAL_ANNUAL_MATCH_DAYS of the episode's ask date. A narrowed run
    (--seasonal-only / --annual-only) keeps the single enabled logic's
    own picks (no intersection possible).

    Phase 1 — COLLECT: gather the complete ask plan (industry MA5-deviation
    episodes with annual-std rate limits, plus broad-index episodes unless
    *market_only*), deduped against stored rows. No requests yet.

    Phase 2 — LOG + RELEASE: the full plan is logged (one line per ask),
    then planning structures are released (del + gc) — from here on the
    run is only requests + DB stores.

    Phase 3 — GUARD + EXECUTE: a plan larger than MAX_PLAN_ASKS raises
    :class:`PlanTooLargeError` unless *force_many_requests*. Execution is
    strictly sequential request -> store, one ask at a time. *interval*,
    *newest_first* and *cut_off* are accepted for CLI compatibility but do
    not shape this flow (episodes are inherently deduped and bounded by
    the annual caps)."""
    end = end or datetime.date.today()
    if end < start:
        raise ValueError(f"end {end} < start {start}")

    market_episodes = await fetch_market_episodes(conn)
    parent_industry_id, parent_sector_id = await fetch_index_parent_tags(conn)
    logger.info("[plan] market asks tagged industry_id=%s sector_id=%s",
                parent_industry_id or "INDEX", parent_sector_id or "NULL")

    plan: List[AskOutcome] = []

    do_seasonal = not (annual_only or market_only)
    do_annual = not (seasonal_only or market_only)
    do_market = not (seasonal_only or annual_only)
    logger.info("[plan] sources: seasonal=%s annual=%s market=%s combine=%s",
                do_seasonal, do_annual, do_market,
                "AND" if do_seasonal and do_annual else "OR")

    # Each logic collects its picks here, keyed (industry_id, side) —
    # with BOTH enabled the plan is their intersection (AND): an ask must
    # be a seasonal top-N pick AND an annual MA5-deviation episode of the
    # same industry+side, matched within SEASONAL_ANNUAL_MATCH_DAYS.
    seasonal_picks: Dict[Tuple[str, str],
                         List[Tuple[datetime.date, IndustrySignal]]] = {}
    annual_picks: Dict[Tuple[str, str],
                       List[Tuple[datetime.date, IndustrySignal]]] = {}

    # ---- Logic 1 — SEASONAL: top-5 hypes/drains per season anchor ----------
    # The UI's "Hypes & Drains" ranking: for every season anchor (the
    # interval-stepped ranking-date grid), ask the top-N HYPE + top-N DRAIN
    # industries the view shows, deduped vs stored rows and the plan.
    if do_seasonal:
        ranking_dates = await fetch_ranking_dates(
            conn, benchmark_code=benchmark_code, period_days=period_days,
            weighting=weighting)
        ranking_dates = [d for d in ranking_dates if start <= d <= end]
        anchors = ranking_dates[::max(1, interval)]
        for anchor in anchors:
            tops = await fetch_top_industries(
                conn, benchmark_code=benchmark_code, period_days=period_days,
                weighting=weighting, as_of=anchor, top_n=top_n)
            for s in tops:
                if industry_ids and s.industry_id not in industry_ids:
                    continue  # per-industry sweep mode
                base = question_base(s.industry_label, s.side)
                asked = await asked_within(
                    conn, industry_id=s.industry_id, base=base, anchor=anchor,
                    window_start=anchor - datetime.timedelta(days=90),
                    window_end=anchor + datetime.timedelta(days=90))
                if asked:
                    continue
                sig = IndustrySignal(date=anchor, side=s.side, rank=s.rank,
                                     industry_id=s.industry_id,
                                     industry_label=s.industry_label,
                                     metric_value=s.metric_value)
                seasonal_picks.setdefault((s.industry_id, s.side), []).append(
                    (anchor, sig))

    # ---- Logic 2 — ANNUAL: per-industry MA5-deviation episodes -------------
    # Rate-limited per industry per calendar year by the annual std of the
    # deviation series (low-vol years <= IND_ANNUAL_LOW_CAP asks, high-vol
    # <= IND_ANNUAL_HIGH_CAP); strongest deviations first. Partitioned by
    # industry_id: one industry's series in memory at a time.
    if do_annual:
        # Partitioned by industry_id: one industry's series in memory at a
        # time; each partition immediately yields its annual-std classified,
        # rate-limited episode picks.
        async for ind, label, year_std, year_cap, ind_episodes in                 iter_industry_episodes(conn,
                                       benchmark_code=benchmark_code):
            if industry_ids and ind not in industry_ids:
                continue
            for year, cap in sorted(year_cap.items()):
                evts = [(ask_date, side, max_dev)
                        for ask_date, (side, max_dev, _s, _e)
                        in ind_episodes.items()
                        if start <= ask_date <= end and ask_date.year == year]
                picked: List[Tuple[datetime.date, str, float]] = []
                for ask_date, side, max_dev in sorted(
                        evts, key=lambda x: -abs(x[2])):
                    if any(abs((ask_date - pd).days) <= 15
                           for pd, _s, _m in picked):
                        continue  # keep asks >= ~2 calendar weeks apart
                    picked.append((ask_date, side, max_dev))
                    if len(picked) >= cap:
                        break
                logger.info("[plan] %s %d: std=%.2f -> cap %d, %d episode(s)",
                            label, year,
                            year_std.get(year, 0.0), cap, len(picked))
                for ask_date, side, max_dev in sorted(picked):
                    sig = IndustrySignal(date=ask_date, side=side, rank=0,
                                         industry_id=ind,
                                         industry_label=label,
                                         metric_value=max_dev)
                    base = question_base(label, side)
                    asked = await asked_within(
                        conn, industry_id=ind, base=base, anchor=ask_date,
                        window_start=ask_date - datetime.timedelta(days=90),
                        window_end=ask_date + datetime.timedelta(days=90))
                    if asked:
                        continue
                    annual_picks.setdefault((ind, side), []).append(
                        (ask_date, sig))

    # ---- combine the industry logics into the plan --------------------------
    # AND (default: both logics enabled) — an annual MA5-deviation episode
    # asks only when the seasonal ranking also picked the same
    # industry+side within SEASONAL_ANNUAL_MATCH_DAYS of the episode's ask
    # date; the ask anchors on the episode date (the move's own as-of).
    # OR (a --seasonal-only / --annual-only narrowed run) — every pick of
    # the enabled logic asks, as before.
    if do_seasonal and do_annual:
        n_joint = 0
        for (ind, side), picks in sorted(annual_picks.items()):
            anchors = [d for d, _s in seasonal_picks.get((ind, side), [])]
            for ask_date, sig in picks:
                if not any(abs((ask_date - a).days)
                           <= SEASONAL_ANNUAL_MATCH_DAYS for a in anchors):
                    continue
                n_joint += 1
                plan.append(AskOutcome(
                    signal=sig, kind="industry",
                    question=build_question(sig.industry_label, side,
                                            ask_date)))
        logger.info("[plan] AND combine: %d annual episode(s) x %d seasonal "
                    "pick(s) -> %d joint ask(s)",
                    sum(len(v) for v in annual_picks.values()),
                    sum(len(v) for v in seasonal_picks.values()), n_joint)
    else:
        for picks in seasonal_picks.values():
            for anchor, sig in picks:
                plan.append(AskOutcome(
                    signal=sig, kind="industry",
                    question=build_question(sig.industry_label, sig.side,
                                            anchor)))
        for picks in annual_picks.values():
            for ask_date, sig in picks:
                plan.append(AskOutcome(
                    signal=sig, kind="industry",
                    question=build_question(sig.industry_label, sig.side,
                                            ask_date)))

    # ---- market plan: broad-index episode asks (000001) ---------------------
    # Per-industry sweep mode (*industry_ids*) plans industry asks only —
    # the broad-market plan belongs to no single industry run.
    if do_market and not industry_ids:
        # Hard annual cap: at most MARKET_ANNUAL_CAP broadmarket asks per
        # calendar year — strongest episodes first.
        by_year: Dict[int, List[Tuple[datetime.date, str, float]]] = {}
        for ask_date in sorted(market_episodes):
            if start <= ask_date <= end:
                side, max_pct = market_episodes[ask_date]
                by_year.setdefault(ask_date.year, []).append(
                    (ask_date, side, max_pct))
        for year in sorted(by_year):
            kept = 0
            for ask_date, side, max_pct in by_year[year]:
                if kept >= MARKET_ANNUAL_CAP:
                    break
                sig = IndustrySignal(date=ask_date, side=side, rank=0,
                                     industry_id=parent_industry_id or "INDEX",
                                     industry_label=MARKET_INDEX_LABEL,
                                     metric_value=max_pct)
                base = market_question_base(side)
                asked = await asked_within(
                    conn, industry_id=sig.industry_id, base=base,
                    anchor=ask_date,
                    window_start=ask_date - datetime.timedelta(days=40),
                    window_end=ask_date + datetime.timedelta(days=40))
                if asked:
                    continue
                plan.append(AskOutcome(signal=sig, kind="market",
                                       question=build_market_question(
                                           side, ask_date)))
                kept += 1

    plan.sort(key=lambda item: item.signal.date)

    # ---- LOG THE FULL PLAN ---------------------------------------------------
    logger.info("[plan] %d ask(s) collected:", len(plan))
    for n, item in enumerate(plan, 1):
        s = item.signal
        if item.kind == "market":
            logger.info("[plan] %3d. market   %s %-5s %s %+.2f%%  %s",
                        n, s.date, s.side, s.industry_label,
                        s.metric_value or 0.0, item.question)
        else:
            logger.info("[plan] %3d. industry %s %-5s %s %+.2f%%  %s", n,
                        s.date, s.side, s.industry_label,
                        s.metric_value or 0.0, item.question)

    # ---- RELEASE PLANNING MEMORY ---------------------------------------------
    del market_episodes
    gc.collect()
    logger.info("[plan] planning memory released (rss %.0f MiB)", _rss_mib())

    # ---- GUARD -----------------------------------------------------------------
    if len(plan) > MAX_PLAN_ASKS:
        if dry_run:
            logger.warning("[plan] %d ask(s) exceed the %d cap — a real run "
                           "would raise PlanTooLargeError (skip with "
                           "--force-many-requests)", len(plan),
                           MAX_PLAN_ASKS)
        elif not force_many_requests:
            raise PlanTooLargeError(len(plan))

    if dry_run:
        logger.info("[plan] dry-run — no asks performed")
        return plan
    if not plan:
        return []

    # ---- EXECUTE: request -> store, one ask at a time --------------------------
    outcomes: List[AskOutcome] = []
    for n, item in enumerate(plan, 1):
        logger.info("[ask %d/%d] %s", n, len(plan), item.question)
        outcome = await ask_one(conn, item.signal, item.question,
                                provider_name=provider_name, model=model,
                                lang=lang, resolve=resolve, kind=item.kind)
        outcomes.append(outcome)
    n_err = sum(1 for c in outcomes if c.error)
    logger.info("[plan] done: %d/%d stored, %d failed",
                len(outcomes) - n_err, len(outcomes), n_err)
    return outcomes
