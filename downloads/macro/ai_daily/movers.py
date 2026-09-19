"""downloads.macro.ai_daily.movers — daily industry movers (plan + asks).

The companion of the broad-market general summary: after it, check WHICH
industries saw significant rises and drops and ask the online-search
summarize agent about each survivor.

The plan reads ONLY the latest daily date of ``stats.cross_stats``
(sec_type='industry', via the ``stats.cross_stats_dates`` map) — the
metric is ``code_price_with_benchmark_offset``: the industry's daily
change minus the broad market's (index points; positive = outperforming
that day). Offsets share one index-point scale per benchmark, so their
RELATIVE gaps are what get compared:

* candidates — the top ``MOVERS_MAX_PER_SIDE`` positive offsets (rises)
  and the most-negative offsets (drops), ranked by magnitude;
* 51% separation gate — a candidate loads only when its offset differs
  from the last-LOADED pick's by more than ``MOVERS_SEPARATION``
  (relative): 2nd vs 1st, then 3rd vs the last loaded. Clustered moves
  tell one story, so only the leader is asked — NOT necessarily loading
  all 3;
* 5d cooldown — an industry+side already asked within the last
  ``MOVERS_COOLDOWN_BD`` trading days (text.llm_qa, category
  ``market_daily``) is skipped; a direction flip is a different question
  and never matches. The cooldown binds the INDUSTRY movers only — the
  broadmarket general summary asks every run regardless (broad tags are
  excluded from the movers plan and never cooldown-checked). The asks'
  search window defaults to the same 5d horizon (``RECENCY_5D`` =
  ``oneWeek``).
* weekly gate — the movers phase is the WEEKLY industry step: it fires
  at most once per ``MOVERS_WEEK_BD`` (5) trading days. When the latest
  industry ask of this category sits inside that 5-trading-day window,
  the run makes NO industry requests (status ``weekly-skip``) — that
  day's ask is the broadmarket general summary only. The DAILY ask is
  broadmarket; the WEEKLY ask is industry.
* staleness guard — when the latest cross_stats date is
  ``MOVERS_MAX_LAG_BD`` (5) BUSINESS days old or more (weekends and
  holidays excluded via the trading calendar), no industry requests are
  made at all (status ``stale``): the move is no longer news.

Each survivor gets its own search+summary ask (the date-embedded
why-did-X-move question) whose outcome carries the ask's FULL reference
list, and the whole block is stored as ``ai_daily_<plan-date>_movers.json``
under the same output dir as the general summary — ``builds.text`` loads
the Q&A into text.llm_qa (+ reference provenance) under the mover's own
industry_id with ``text.llm_qa.qa_date`` = the plan date, and the ask
hits into text.news.

Best run after the market close AND after ``python -m
builds.cross_stats`` has loaded the day's industry grain. Recency-only
by design — historical sector asks belong to the HISTORY flow
(``llm_agents.industry_qa_weekly``'s manual backfill, seasonal top +
MA5-slope episodes).
"""
from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from llm_agents.online_search_summary import (
    DAILY_CATEGORY, DAILY_COUNT, RECENCY_5D, SHANGHAI_TZ,
    SearchOptions, SearchSummary, daily_recency, get_provider,
)
from _common._holidays_and_weekdays import recent_trading_day_cutoff

logger = logging.getLogger("ai_daily")

# --- plan constants ---------------------------------------------------------
# Benchmark of the industry-grain offsets: 930903 = 中证A股 (the all-A-share
# index — the broadest broad market in stats.cross_stats industry grain).
MOVERS_BENCHMARK = "930903"
MOVERS_MAX_PER_SIDE = 3        # 3 rises and 3 downs AT MOST
MOVERS_SEPARATION = 0.51       # consecutive ranks must differ by > 51%
# 5d cooldown (business days, matching the recency flow's 5d default
# window): an industry+side asked within the last 5 trading days is
# skipped. A direction flip is a different question and never matches.
MOVERS_COOLDOWN_BD = 5
# Weekly industry step: the movers phase fires at most once per 5
# trading days. The DAILY ask is the broadmarket general summary (no
# gate, no cooldown); the WEEKLY ask is the industry movers — the 5d
# cooldown IS the week.
MOVERS_WEEK_BD = 5
# Staleness guard: when the latest cross_stats date is this many business
# days old or older (holidays/weekends excluded — trading-day counting),
# the move is no longer news: NO industry requests are made.
MOVERS_MAX_LAG_BD = 5
COOLDOWN_CATEGORY = DAILY_CATEGORY

SIDE_RISE = "rise"
SIDE_DROP = "drop"
_SIDE_VERB = {SIDE_RISE: "上涨", SIDE_DROP: "下跌"}

# Broad-market classification tags (BROAD_SSE, BROAD_CSI_A, …). The
# movers plan is INDUSTRY-only: the broadmarket ask is the general
# summary's job and runs every run — the 5d cooldown (and the plan
# itself) never touches it.
BROAD_TAG_PREFIX = "BROAD"

# Latest industry-grain offsets for one benchmark, on the LATEST daily
# cross_stats date (the dates map avoids scanning the hash-partitioned
# main table). Broad-market tags are excluded at the source — the movers
# universe is daily industry only.
LATEST_OFFSETS_SQL = """
    SELECT cs.code AS industry_id,
           cs.code_price_with_benchmark_offset AS offset
    FROM stats.cross_stats cs
    WHERE cs.sec_type = 'industry'
      AND cs.benchmark_code = $1::text
      AND cs.date = (SELECT max(date) FROM stats.cross_stats_dates)
      AND cs.code_price_with_benchmark_offset IS NOT NULL
      AND cs.code NOT LIKE 'BROAD%'
"""

LATEST_DATE_SQL = "SELECT max(date) FROM stats.cross_stats_dates"

# Industry label for a mover's question (one label per picked id).
INDUSTRY_LABELS_SQL = """
    SELECT DISTINCT ON (industry_id) industry_id, industry_label
    FROM stats.sec_classification
    WHERE industry_id = ANY($1::text[]) AND industry_label IS NOT NULL
    ORDER BY industry_id, industry_label
"""

# 5d cooldown: this job's own prior ask of the same industry+side (the
# 上涨/下跌 verb is embedded in the question text) within the window.
IN_COOLDOWN_SQL = """
    SELECT EXISTS (
        SELECT 1 FROM text.llm_qa
        WHERE category = $1::text
          AND industry_id = $2::text
          AND (qa_date AT TIME ZONE 'Asia/Shanghai')::date >= $3::date
          AND question LIKE $4::text
    )
"""

# Weekly gate: the latest industry ask of this job's category (the
# general summary's BROAD* tag is excluded — it is the daily ask and
# never gates the industry step).
LAST_MOVERS_ASK_SQL = """
    SELECT max((qa_date AT TIME ZONE 'Asia/Shanghai')::date)
    FROM text.llm_qa
    WHERE category = $1::text
      AND industry_id IS NOT NULL
      AND industry_id NOT LIKE 'BROAD%'
"""

# The broad-market industry_id the general summary is stored under by
# default (上证指数 000001's parent classification tag).
BROAD_INDUSTRY_SQL = """
    SELECT industry_id FROM stats.sec_index_tags
    WHERE code = '000001' AND industry_id IS NOT NULL
      AND industry_id != 'benchmark_broadmarket'
    LIMIT 1
"""


@dataclass
class MoverPick:
    """One candidate mover (post-gate, pre/post cooldown)."""
    industry_id: str
    label: str
    offset: float
    side: str                      # rise | drop
    rank: int                      # 1-based within its side
    question: str = ""
    cooldown: bool = False         # True = suppressed by the 5d cooldown

    def to_dict(self) -> Dict[str, Any]:
        return {
            "industry_id": self.industry_id, "label": self.label,
            "offset": self.offset, "side": self.side, "rank": self.rank,
            "question": self.question, "cooldown": self.cooldown,
        }


# ----------------------------------------------------------------------------
# Pure plan helpers
# ----------------------------------------------------------------------------
def plan_is_stale(
    plan_date: Optional[datetime.date],
    today: Optional[datetime.date] = None,
) -> bool:
    """True when the latest cross_stats date is too old to ask about.

    Old means ``MOVERS_MAX_LAG_BD`` (5) business days or more behind the
    most recent session (weekends/holidays excluded — the trading-day
    window ``[plan_date, last_business_day(today)]`` spans 5 or fewer
    trading days only while the data is fresh). A stale plan date makes
    NO industry requests — the move is no longer news.
    """
    if plan_date is None:
        return True
    today = today or datetime.datetime.now(SHANGHAI_TZ).date()
    return plan_date <= recent_trading_day_cutoff(MOVERS_MAX_LAG_BD, today)


def movers_due(
    last_ask_date: Optional[datetime.date],
    today: Optional[datetime.date] = None,
) -> bool:
    """True when a weekly industry ask-run is due.

    The weekly gate: the trading-day span between ask-runs must cover 5
    trading days counting the ask day itself (``cutoff`` is the
    5th-most-recent trading day, so ``[cutoff, today]`` spans exactly
    ``MOVERS_WEEK_BD`` days and an ask strictly inside it suppresses the
    phase). An ask therefore repeats on the same weekday of the next
    trading week — one industry step per 5-trading-day week, the same
    ask-day-inclusive boundary the per-pick 5d cooldown applies.
    """
    today = today or datetime.datetime.now(SHANGHAI_TZ).date()
    if last_ask_date is None:
        return True
    return last_ask_date <= recent_trading_day_cutoff(MOVERS_WEEK_BD, today)


def _gate_loaded(sorted_picks: List[Tuple[str, float]],
                 separation: float) -> List[Tuple[str, float]]:
    """Apply the 51% separation gate over one side's ranked candidates.

    Prefix-loading: the leader always loads; each next candidate loads
    only when it differs from the LAST-LOADED pick by more than
    *separation* (relative to the loaded pick's |offset|). Near-tied
    followers therefore never load — the asks kept are the clearly
    distinct stories.
    """
    loaded: List[Tuple[str, float]] = []
    for ind, chg in sorted_picks:
        if not loaded:
            loaded.append((ind, chg))
            continue
        prev = loaded[-1][1]
        if prev == 0:                     # degenerate: any move differs
            if chg != 0:
                loaded.append((ind, chg))
            continue
        if abs(prev - chg) / abs(prev) > separation:
            loaded.append((ind, chg))
    return loaded


def build_mover_question(label: str, side: str,
                         plan_date: Optional[datetime.date]) -> str:
    """The date-embedded why-did-this-sector-move question."""
    d = plan_date or datetime.date.today()
    return (f"{d.month}月{d.day}日{label}板块"
            f"显著{_SIDE_VERB[side]}的原因是什么？")


def pick_movers(
    offsets: Dict[str, float],
    *,
    separation: float = MOVERS_SEPARATION,
    max_per_side: int = MOVERS_MAX_PER_SIDE,
    labels: Optional[Dict[str, str]] = None,
    plan_date: Optional[datetime.date] = None,
) -> Dict[str, List[MoverPick]]:
    """Rank the latest-date offsets into rise/drop candidates + gate.

    Returns ``{"rises": [...], "drops": [...]}`` — each list at most
    *max_per_side* long (3 rises and 3 downs AT MOST) and possibly much
    shorter: the separation gate decides how many of each side actually
    load. Rises take the top positive offsets, drops the most-negative.
    """
    labels = labels or {}
    rises = sorted(((c, v) for c, v in offsets.items() if v > 0),
                   key=lambda x: -x[1])[:max_per_side]
    drops = sorted(((c, v) for c, v in offsets.items() if v < 0),
                   key=lambda x: x[1])[:max_per_side]
    out: Dict[str, List[MoverPick]] = {}
    for side, ranked in ((SIDE_RISE, rises), (SIDE_DROP, drops)):
        picks: List[MoverPick] = []
        for rank, (ind, chg) in enumerate(_gate_loaded(ranked, separation),
                                          start=1):
            label = labels.get(ind, ind)
            picks.append(MoverPick(
                industry_id=ind, label=label, offset=chg, side=side,
                rank=rank,
                question=build_mover_question(label, side, plan_date)))
        out[f"{side}s"] = picks
    return out


# ----------------------------------------------------------------------------
# DB-backed plan
# ----------------------------------------------------------------------------
async def fetch_latest_offsets(
    conn, benchmark: str = MOVERS_BENCHMARK,
) -> Tuple[Optional[datetime.date], Dict[str, float]]:
    """(latest cross_stats date, {industry_id: offset}) for *benchmark*."""
    plan_date = await conn.fetchval(LATEST_DATE_SQL)
    if plan_date is None:
        return None, {}
    rows = await conn.fetch(LATEST_OFFSETS_SQL, benchmark)
    return plan_date, {r["industry_id"]: float(r["offset"]) for r in rows}


async def fetch_labels(conn, industry_ids: List[str]) -> Dict[str, str]:
    """{industry_id: label} for the picked ids (id itself when absent)."""
    if not industry_ids:
        return {}
    rows = await conn.fetch(INDUSTRY_LABELS_SQL, industry_ids)
    return {r["industry_id"]: r["industry_label"] for r in rows}


async def apply_cooldown(
    conn, picks: List[MoverPick], plan_date: datetime.date,
    *, category: str = COOLDOWN_CATEGORY,
    cooldown_bd: int = MOVERS_COOLDOWN_BD,
) -> None:
    """Flag picks asked within the cooldown window (industry+side).

    BUSINESS-day window: ``[cutoff, plan_date]`` spans exactly
    ``cooldown_bd + 1`` trading days — the plan date itself plus the
    last ``cooldown_bd`` (5) trading days, weekends/holidays excluded.
    Direction flips never match (the 上涨/下跌 verb is part of the
    match). INDUSTRY-ONLY by contract: broad-market tags are skipped
    (they are excluded from the movers plan anyway) and the broadmarket
    general summary never consults this cooldown — it asks every run.
    """
    window_start = recent_trading_day_cutoff(cooldown_bd + 1, plan_date)
    for p in picks:
        if p.industry_id.startswith(BROAD_TAG_PREFIX):
            continue  # never cooldown-suppressed
        pattern = f"%{_SIDE_VERB[p.side]}%"
        p.cooldown = await conn.fetchval(
            IN_COOLDOWN_SQL, category, p.industry_id, window_start, pattern)


async def broad_industry_id(conn) -> Optional[str]:
    """The broad-market industry_id the general summary stores under."""
    return await conn.fetchval(BROAD_INDUSTRY_SQL)


# ----------------------------------------------------------------------------
# Ask + persist
# ----------------------------------------------------------------------------
async def run_movers(
    conn,
    *,
    benchmark: str = MOVERS_BENCHMARK,
    provider_name: str = "zhipu",
    model: Optional[str] = None,
    lang: str = "zh",
    count: Optional[int] = None,
    category: str = COOLDOWN_CATEGORY,
) -> Dict[str, Any]:
    """Plan the movers off the latest cross_stats date, ask each survivor.

    Gate order: no-data -> stale (cross_stats too old) -> weekly-skip (an
    industry ask inside the last MOVERS_WEEK_BD trading days) -> plan,
    per-pick 5d cooldown, ask. Returns the movers envelope: the plan
    (candidates + cooldown flags), the per-ask outcomes (question, answer,
    full reference list) and the failures. A failed ask never kills the
    batch — its outcome carries the error. *conn* is an open pool; the
    caller owns opening/closing it.
    """
    plan_date, offsets = await fetch_latest_offsets(conn, benchmark)
    if plan_date is None or not offsets:
        return {"status": "no-data", "benchmark": benchmark,
                "plan_date": plan_date.isoformat()
                if plan_date else None,
                "candidates": {}, "asked": [], "failed": []}
    if plan_is_stale(plan_date):
        logger.warning("movers: latest cross_stats date %s is >= %d "
                       "business days old — skipping industry requests",
                       plan_date, MOVERS_MAX_LAG_BD)
        return {"status": "stale", "benchmark": benchmark,
                "plan_date": plan_date.isoformat(),
                "max_lag_bd": MOVERS_MAX_LAG_BD,
                "candidates": {}, "asked": [], "failed": []}

    # Weekly gate: industry asks are the WEEKLY step. An industry ask of
    # this category inside the last MOVERS_WEEK_BD trading days makes
    # today's run broadmarket-only (the general summary asks regardless).
    last_ask_date = await conn.fetchval(LAST_MOVERS_ASK_SQL, category)
    if not movers_due(last_ask_date):
        logger.info("movers: last industry ask %s is inside the %d-"
                    "trading-day weekly window — broadmarket summary only",
                    last_ask_date, MOVERS_WEEK_BD)
        return {"status": "weekly-skip", "benchmark": benchmark,
                "plan_date": plan_date.isoformat(),
                "week_bd": MOVERS_WEEK_BD,
                "last_ask_date": last_ask_date.isoformat(),
                "candidates": {}, "asked": [], "failed": []}

    labels = await fetch_labels(conn, list(offsets))
    plan = pick_movers(offsets, labels=labels, plan_date=plan_date)
    for side in ("rises", "drops"):
        await apply_cooldown(conn, plan[side], plan_date, category=category)

    n_cool = sum(1 for s in ("rises", "drops")
                 for p in plan[s] if p.cooldown)
    due = [p for s in ("rises", "drops") for p in plan[s]
           if not p.cooldown]
    logger.info("movers plan %s [%s]: %d rise(s) / %d drop(s) loaded, "
                "%d on cooldown, %d to ask", plan_date, benchmark,
                len(plan["rises"]), len(plan["drops"]), n_cool, len(due))

    opts = SearchOptions(
        count=count if count is not None else DAILY_COUNT,
        recency=daily_recency(plan_date), content_size="high")
    provider = get_provider(provider_name)()

    asked: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for p in due:
        try:
            summary: SearchSummary = await provider.summarize(
                p.question, model=model, opts=opts, lang=lang)
            outcome: Dict[str, Any] = {
                **p.to_dict(),
                "answer": summary.answer,
                "cited_refs": summary.cited_refs,
                "n_refs": len(summary.hits),
                # The ask's FULL hit list — builds.text resolves these into
                # text.news rows + text.llm_qa provenance, so the artifact
                # must be self-sufficient (the pre-removal --store path got
                # them from the live response instead).
                "references": [h.to_dict() for h in summary.hits],
                "provider": summary.provider, "model": summary.model,
            }
            asked.append(outcome)
            logger.info("  [%s] %s -> ok (%s)", p.side, p.question,
                        ", ".join(summary.cited_refs[:3]) or "no cites")
        except Exception as e:  # one failed ask must not kill the batch
            failed.append({**p.to_dict(),
                           "error": f"{type(e).__name__}: {e}"})
            logger.warning("  [%s] %s -> FAILED %s: %s", p.side, p.question,
                           type(e).__name__, e)

    return {
        "status": "ok",
        "benchmark": benchmark,
        "plan_date": plan_date.isoformat(),
        "candidates": {
            "rises": [p.to_dict() for p in plan["rises"]],
            "drops": [p.to_dict() for p in plan["drops"]],
        },
        "asked": asked,
        "failed": failed,
    }


# ----------------------------------------------------------------------------
# Artifact cache
# ----------------------------------------------------------------------------
def movers_artifact_path(out_dir: Path,
                         plan_date: datetime.date) -> Path:
    return out_dir / (
        f"ai_daily_{plan_date.strftime('%Y-%m-%d')}_movers.json")


def movers_cached(path: Path) -> bool:
    """True when *path* holds a completed movers run for the plan date."""
    if not path.exists() or not path.is_file():
        return False
    try:
        with path.open("r", encoding="utf-8") as f:
            obj = json.load(f)
    except (ValueError, OSError):
        return False
    return (isinstance(obj, dict) and obj.get("status") == "ok"
            and bool(obj.get("plan_date"))
            and isinstance(obj.get("asked"), list))
