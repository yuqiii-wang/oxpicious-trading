"""downloads.macro.ai_daily.coverage_backfill — the coverage-gap movers backfill.

The HISTORY counterpart of the live movers phase (``movers.py`` is
deliberately recency-only — latest cross_stats date, weekly gate): when
the News Text States corpus leaves an industry SIGNIFICANTLY INSUFFICIENT
in news coverage, this job asks the online-search summarize agent why
that industry saw its historical big rises and drops, so the answers and
their search references populate ``text.news`` / ``llm.llm_qa`` on the
dates the panel is blind to.

Flow (``python -m downloads.macro.ai_daily.coverage_backfill``):

1. **Study** — per-industry coverage totals over ``text.news_coverage_audit``
   LEFT JOINed onto the ASKABLE universe (``stats.cross_stats``
   ``sec_type='industry'`` codes vs the movers benchmark — an industry
   without an index history cannot be asked why it moved). The
   insufficiency cut: total articles <= ``--insufficient-frac`` × the
   universe's median (default 0.25), or the absolute
   ``--insufficient-max-articles``. Log-only: no study table.
2. **Plan** — per flagged industry, rank its offset history
   (``code_price_with_benchmark_offset``; the movers metric) by |offset|
   and greedy-pick at most ``--per-side`` rises + drops, one ask per
   episode: candidates closer than ``--min-date-gap-days`` to an already
   picked date of the same industry+side never load, a date the industry
   already has ``--covered-skip-articles`` articles for within the ask
   window ``[date - window, date]`` is skipped (the hole is already
   filled), and an industry+date+side already asked — ``(question,
   qa_day)`` in ``llm.llm_qa`` (category ``market_daily``; the movers
   questions are date-embedded so the keys match) or recorded in an
   existing backfill artifact — is skipped (``--force`` re-asks stored
   ones; ``--retry-failed`` also retries previously FAILED asks, which
   are otherwise skipped for good: a date with no reachable news must
   not retry forever). A global ``--max-asks`` budget caps every run;
   picks ask oldest-first.
3. **Ask** — per pick, the movers question (``build_mover_question``) is
   searched with ``recency="noLimit"`` (the provider's recency knob is
   relative to NOW — a historical ask would otherwise find nothing),
   then the hits go through the date-anchored post-filter
   (:class:`~llm_agents.online_search_summary.storage.matching.RefRecencyFilter`
   with ``anchor`` = the move date): every hit whose KNOWN publish date
   falls outside ``[move_date - window, move_date]`` is dropped — news
   published AFTER the searched date never enters the artifact — and the
   survivors are summarized via the compose seam with ``anchor_date`` =
   the move date (the prompt pins the target date and the stale-text
   scan anchors on it). One failed ask never kills the batch.
4. **Write** — movers-shaped standalone envelopes, one per move date,
   MERGED into ``temps/ai_daily/ai_daily_<date>_backfill_movers.json``
   (asked/failed dedupe on the question inside the envelope). The name
   matches the ``builds.text`` loader's ``ai_daily_*_movers.json`` glob,
   so the existing load path persists everything: answer rows + reference
   hits into ``text.news`` (source ``ai_daily``, dated the move date),
   the Q&A into ``llm.llm_qa`` (``qa_date`` = the move date, the ask's
   own ``industry_id``) with ``llm.llm_qa_refs`` provenance — and
   ``ai_daily_qa``'s ``in_ref_window`` drop already guarantees a stored
   ref's date sits in ``[qa_day - 20d, qa_day]``. Historical asks are
   invisible to the live flow's weekly gate and 5d cooldown (both gate
   on recent ``qa_date``s; old rows sort out of every max window).

Like every downloader this module NEVER writes the database — run
``python -m builds.text`` afterwards to load the artifacts (the run
summary reminds).

Usage::

    python -m downloads.macro.ai_daily.coverage_backfill
    python -m downloads.macro.ai_daily.coverage_backfill --max-asks 6
    python -m downloads.macro.ai_daily.coverage_backfill --insufficient-max-articles 20
    python -m downloads.macro.ai_daily.coverage_backfill --retry-failed --force
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from downloads._common import DownloadJob, resolve_out_dir, setup_logger
from downloads.macro.ai_daily import movers
from downloads.macro.ai_daily.data_definitions import MoverPick
from llm_agents.online_search_summary import (
    DAILY_COUNT, SearchOptions, get_provider,
)
from llm_agents.online_search_summary.core.errors import OnlineSearchError
from llm_agents.online_search_summary.storage.matching import (
    REF_WINDOW_DAYS, RefRecencyFilter,
)

logger = setup_logger("ai_daily_coverage_backfill")

OUTPUT_DIR_NAME = "ai_daily"

# The backfill's artifact suffix — INSIDE the loader's
# ``ai_daily_*_movers.json`` glob but distinct from the live flow's
# ``ai_daily_<date>_movers.json`` names, so a backfill envelope never
# collides with (or satisfies) the live movers artifact cache.
BACKFILL_SUFFIX = "_backfill_movers.json"

# The run summary envelope marker — the ai_daily convention (the logger
# stream also writes stdout, so API callers scan for the LAST marker
# line).
RESULT_MARKER = "@@AI_DAILY_JSON@@"

# --- plan defaults ----------------------------------------------------------
DEFAULT_INSUFFICIENT_FRAC = 0.25   # total <= frac × universe median
DEFAULT_PER_SIDE = 2               # at most 2 rises + 2 drops per industry
DEFAULT_MIN_DATE_GAP_DAYS = 10     # calendar days between picked dates of
#                                    one industry+side (one ask per episode)
DEFAULT_WINDOW_DAYS = REF_WINDOW_DAYS  # the ask window: [date-20d, date]
DEFAULT_MAX_ASKS = 20              # search+LLM asks per run at most


# ----------------------------------------------------------------------------
# SQL (module-local, the movers.py convention — reads only)
# ----------------------------------------------------------------------------
# The askable universe (industries with an offset history) LEFT JOINed with
# their coverage totals — an industry with no coverage rows counts 0 (the
# maximally insufficient case). '000001' rollup, '' untagged and BROAD*
# tags are never industries here.
STUDY_SQL = """
    WITH universe AS (
        SELECT DISTINCT cs.code AS industry_id
        FROM stats.cross_stats cs
        WHERE cs.sec_type = 'industry'
          AND cs.benchmark_code = $1::text
          AND cs.code_price_with_benchmark_offset IS NOT NULL
    ),
    cov AS (
        SELECT nca.industry_id,
               SUM(nca.n_articles) AS n_articles,
               SUM(nca.n_digested) AS n_digested
        FROM text.news_coverage_audit nca
        WHERE nca.industry_id NOT IN ('000001', '')
          AND nca.industry_id NOT LIKE 'BROAD%'
        GROUP BY nca.industry_id
    )
    SELECT u.industry_id,
           COALESCE(cov.n_articles, 0)::bigint AS n_articles,
           COALESCE(cov.n_digested, 0)::bigint AS n_digested
    FROM universe u
    LEFT JOIN cov ON cov.industry_id = u.industry_id
    ORDER BY n_articles, u.industry_id
"""

# Coverage-only industries — tagged in the audit but without an offset
# history (strategy themes, non-askable tags): reported, never planned.
COVERAGE_ONLY_SQL = """
    SELECT DISTINCT nca.industry_id
    FROM text.news_coverage_audit nca
    WHERE nca.industry_id NOT IN ('000001', '')
      AND nca.industry_id NOT LIKE 'BROAD%'
      AND nca.industry_id NOT IN (
          SELECT DISTINCT cs.code FROM stats.cross_stats cs
          WHERE cs.sec_type = 'industry'
            AND cs.benchmark_code = $1::text
            AND cs.code_price_with_benchmark_offset IS NOT NULL)
"""

# One industry's full offset history (the movers metric, all dates).
HISTORY_SQL = """
    SELECT cs.date, cs.code_price_with_benchmark_offset AS offset
    FROM stats.cross_stats cs
    WHERE cs.sec_type = 'industry'
      AND cs.benchmark_code = $1::text
      AND cs.code = $2::text
      AND cs.code_price_with_benchmark_offset IS NOT NULL
    ORDER BY cs.date
"""

# The flagged industries' per-day coverage counts (the covered-skip check).
COVERAGE_DAYS_SQL = """
    SELECT industry_id, date, n_articles
    FROM text.news_coverage_audit
    WHERE industry_id = ANY($1::text[])
      AND industry_id NOT IN ('000001', '')
"""

# Already-stored asks of the movers category: (question, qa_day) — the
# backfill questions are date-embedded, so this is the exact incremental
# key (the same shape ai_daily_qa's stored-key scan uses).
ASKED_KEYS_SQL = """
    SELECT question,
           (qa_date AT TIME ZONE 'Asia/Shanghai')::date AS qa_day
    FROM llm.llm_qa
    WHERE category = $1::text
"""


def backfill_artifact_path(out_dir: Path, move_date: datetime.date) -> Path:
    """The backfill envelope path for one move date (the merged artifact)."""
    return out_dir / (
        f"ai_daily_{move_date.strftime('%Y-%m-%d')}{BACKFILL_SUFFIX}")


@dataclasses.dataclass
class BackfillPick(MoverPick):
    """A backfill pick: the mover question + its concrete move date.

    :class:`MoverPick` carries the question, side and rank; the backfill
    needs the PLAN's move date at ask/write time (the post-filter's
    anchor, the compose prompt's ``anchor_date``, the envelope's
    ``plan_date``). The question embeds the date, but a re-parse would
    be fragile — the plan carries it explicitly, and ``to_dict`` stamps
    it as ``ask_date`` on the artifact outcomes so every envelope entry
    is self-describing.
    """

    move_date: datetime.date = datetime.date(1970, 1, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {**super().to_dict(), "ask_date": self.move_date.isoformat()}


def _entry_ask_date(entry: Dict[str, Any]) -> datetime.date:
    """The move date an outcome entry carries (the ask phase stamps it)."""
    return datetime.date.fromisoformat(entry["ask_date"])


def _existing_backfill_asks(
    out_dir: Path,
) -> Dict[Tuple[str, datetime.date], bool]:
    """{(question, move_date): succeeded} across existing backfill artifacts.

    The artifact-level dedupe layer beside the llm_qa keys: FAILED asks
    are recorded here too, so a date with no reachable news skips for
    good (``--retry-failed`` drops the failed ones from the skip set).
    """
    asks: Dict[Tuple[str, datetime.date], bool] = {}
    for path in sorted(out_dir.glob(f"ai_daily_*{BACKFILL_SUFFIX}")):
        try:
            with path.open("r", encoding="utf-8") as f:
                env = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(env, dict):
            continue
        plan_date = env.get("plan_date")
        if not isinstance(plan_date, str):
            continue
        try:
            day = datetime.date.fromisoformat(plan_date)
        except ValueError:
            continue
        for key in ("asked", "failed"):
            for entry in env.get(key) or []:
                if not isinstance(entry, dict):
                    continue
                question = (entry.get("question") or "").strip()
                if question:
                    asks[(question, day)] = key == "asked"
    return asks


class CoverageStudy:
    """The study step's typed outcome (log + run-summary carrier)."""

    def __init__(
        self,
        *,
        benchmark: str,
        median: float,
        cut: float,
        totals: List[Tuple[str, int, int]],
        flagged: List[Tuple[str, int, int]],
        coverage_only: List[str],
    ) -> None:
        self.benchmark = benchmark
        self.median = median
        self.cut = cut
        self.totals = totals
        self.flagged = flagged
        self.coverage_only = coverage_only

    def summary_dict(self) -> Dict[str, Any]:
        """The marker-envelope study block (API-consumable summary)."""
        return {
            "status": "ok",
            "phase": "coverage_backfill",
            "benchmark": self.benchmark,
            "n_askable": len(self.totals),
            "coverage_median": self.median,
            "insufficiency_cut": self.cut,
            "flagged": [
                {"industry_id": ind, "n_articles": total, "n_digested": dig}
                for ind, total, dig in self.flagged
            ],
            "coverage_only": self.coverage_only,
        }


class CoverageBackfill(DownloadJob):
    """The coverage-gap movers backfill job — study, plan, ask, write.

    Read-only against the database (asyncpg pool via
    ``_common.build_commons.get_db_or_exit``); the ask outcomes land in
    movers-shaped artifact envelopes under ``temps/ai_daily/`` and reach
    the DB only through ``python -m builds.text``.
    """

    description = (
        "Coverage-gap movers backfill: ask the online-search agent why "
        "news-insufficient industries saw their historical big rises/"
        "drops (stats.cross_stats offsets), artifacts under temps/ai_daily/.")
    component = "ai_daily_coverage_backfill"
    service = "ai_daily_coverage_backfill"

    # ------------------------------------------------------------------
    # CLI
    # ------------------------------------------------------------------
    def add_arguments(self, parser) -> None:
        parser.add_argument("--provider", default="zhipu",
                            help="Online-search provider (default zhipu).")
        parser.add_argument("--model", default=None,
                            help="Chat model for the summary (provider default).")
        parser.add_argument("--lang", default="zh",
                            help="Summary answer language (default zh).")
        parser.add_argument("--count", type=int, default=None,
                            help=f"References per search (default: the daily "
                                 f"preset's {DAILY_COUNT}).")
        parser.add_argument("--benchmark", default=movers.MOVERS_BENCHMARK,
                            help=f"Benchmark of the industry-grain offsets "
                                 f"(default {movers.MOVERS_BENCHMARK} = 中证A股).")
        parser.add_argument("--insufficient-frac", type=float,
                            default=DEFAULT_INSUFFICIENT_FRAC,
                            help=("Insufficiency cut: total articles <= this "
                                  "fraction of the askable universe's median "
                                  f"(default {DEFAULT_INSUFFICIENT_FRAC})."))
        parser.add_argument("--insufficient-max-articles", type=int, default=None,
                            help="Alternative absolute cut: flag industries "
                                 "with at most this many articles total.")
        parser.add_argument("--per-side", type=int, default=DEFAULT_PER_SIDE,
                            help=f"Big-move picks per industry per side at "
                                 f"most (default {DEFAULT_PER_SIDE}).")
        parser.add_argument("--min-date-gap-days", type=int,
                            default=DEFAULT_MIN_DATE_GAP_DAYS,
                            help=("Calendar days between picked dates of one "
                                  "industry+side (default "
                                  f"{DEFAULT_MIN_DATE_GAP_DAYS})."))
        parser.add_argument("--window-days", type=int,
                            default=DEFAULT_WINDOW_DAYS,
                            help=f"Ask window [date - N, date] for the hit "
                                 f"filter and the covered-skip check (default "
                                 f"{DEFAULT_WINDOW_DAYS}).")
        parser.add_argument("--covered-skip-articles", type=int, default=1,
                            help="Skip a big date when the industry already "
                                 "has this many articles inside the ask "
                                 "window (default 1; 0 disables the skip).")
        parser.add_argument("--max-asks", type=int, default=DEFAULT_MAX_ASKS,
                            help=f"Search+LLM asks per run at most (default "
                                 f"{DEFAULT_MAX_ASKS}).")
        parser.add_argument("--retry-failed", action="store_true",
                            help="Retry asks recorded as FAILED in existing "
                                 "backfill artifacts (they are skipped for "
                                 "good otherwise).")
        parser.add_argument("--force", action="store_true",
                            help="Re-ask questions already stored in "
                                 "llm.llm_qa (the upserts are idempotent — "
                                 "the stored rows just refresh).")
        parser.add_argument("--out-root", type=str, default=None,
                            help="Output root dir. Default: "
                                 "<project>/temps/ai_daily")

    def apply_args(self) -> None:
        if not 0 < self.args.insufficient_frac <= 1:
            self.parser.error("--insufficient-frac must be in (0, 1]")
        if self.args.per_side < 1:
            self.parser.error("--per-side must be >= 1")
        if self.args.min_date_gap_days < 1:
            self.parser.error("--min-date-gap-days must be >= 1")
        if not 1 <= self.args.window_days <= 60:
            self.parser.error("--window-days must be 1-60")
        if self.args.covered_skip_articles < 0:
            self.parser.error("--covered-skip-articles must be >= 0")
        if self.args.max_asks < 1:
            self.parser.error("--max-asks must be >= 1")
        if self.args.count is not None and not 1 <= self.args.count <= 50:
            self.parser.error("--count must be 1-50")

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------
    def run(self) -> None:
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        from _common.build_commons import get_db_or_exit

        conn = await get_db_or_exit()
        try:
            study = await self.study(conn)
            picks = await self.plan(conn, study)
            asked: List[Dict[str, Any]] = []
            failed: List[Dict[str, Any]] = []
            if picks:
                outcomes = await self.ask(picks)
                asked, failed = outcomes["asked"], outcomes["failed"]
            else:
                logger.info("backfill: no due picks — nothing to ask")
            artifacts = self.write_artifacts(
                {"asked": asked, "failed": failed}, study)
            summary = study.summary_dict()
            summary["asked"] = len(asked)
            summary["failed"] = len(failed)
            summary["artifacts"] = artifacts
            logger.info("backfill done: %d asked, %d failed -> %s",
                        summary["asked"], summary["failed"],
                        ", ".join(artifacts) or "no artifacts")
            print(RESULT_MARKER + json.dumps(summary, ensure_ascii=False),
                  flush=True)
            logger.info("run `python -m builds.text` to load the artifacts "
                        "into text.news / llm.llm_qa")
        finally:
            from _common.db_commons import close_pools_async

            await close_pools_async()

    # ------------------------------------------------------------------
    # 1. Study — the cross-section coverage read
    # ------------------------------------------------------------------
    async def study(self, conn) -> CoverageStudy:
        """Per-industry coverage totals vs the insufficiency cut.

        The universe is the ASKABLE industries (offset history present);
        the median covers it including the zero-coverage members. The
        cut: ``n_articles <= frac * median`` or, when set, the absolute
        ``--insufficient-max-articles``.
        """
        rows = await conn.fetch(STUDY_SQL, self.args.benchmark)
        totals = [
            (r["industry_id"], int(r["n_articles"]), int(r["n_digested"]))
            for r in rows
        ]
        median = statistics.median([t for _, t, _ in totals]) if totals else 0
        max_articles = self.args.insufficient_max_articles
        cut = max_articles if max_articles is not None \
            else self.args.insufficient_frac * median
        flagged = [
            (ind, total, dig) for ind, total, dig in totals if total <= cut
        ]
        coverage_only = [
            r["industry_id"] for r in await conn.fetch(
                COVERAGE_ONLY_SQL, self.args.benchmark)
        ]
        logger.info("study [%s]: %d askable industries, coverage median=%.0f, "
                    "cut=%s -> %d flagged",
                    self.args.benchmark, len(totals), median,
                    f"<={max_articles}" if max_articles is not None
                    else f"<={cut:.1f} "
                         f"({self.args.insufficient_frac:.2f} × median)",
                    len(flagged))
        for ind, total, dig in flagged:
            logger.info("  flagged %-14s articles=%-5d digested=%d",
                        ind, total, dig)
        if coverage_only:
            logger.info("  %d coverage-only industry ids have no offset "
                        "history (themes/tags — never planned): %s",
                        len(coverage_only), ", ".join(coverage_only))
        return CoverageStudy(
            benchmark=self.args.benchmark, median=float(median), cut=cut,
            totals=totals, flagged=flagged, coverage_only=coverage_only)

    # ------------------------------------------------------------------
    # 2. Plan — big-move dates per flagged industry
    # ------------------------------------------------------------------
    async def plan(self, conn, study: CoverageStudy) -> List[BackfillPick]:
        """The due asks: per flagged industry, the uncovered big-move dates.

        Greedy per side over the |offset|-ranked history; a candidate
        loads only when it clears every gate: the date gap to the
        already-picked dates of its industry+side, the covered-skip
        check (the industry's own coverage rows inside
        ``[date - window, date]``), and the asked-already check
        (llm.llm_qa stored keys + existing backfill artifacts, failed
        ones included unless ``--retry-failed``; stored ones unless
        ``--force``).
        """
        flagged_ids = [ind for ind, _, _ in study.flagged]
        if not flagged_ids:
            return []
        labels = await movers.Movers().fetch_labels(conn, flagged_ids)
        cov_days = await self._coverage_days(conn, flagged_ids)
        stored_keys = await self._stored_ask_keys(conn)
        artifact_asks = _existing_backfill_asks(self._out_dir())
        window = datetime.timedelta(days=self.args.window_days)

        picks: List[BackfillPick] = []
        for ind, _total, _dig in study.flagged:
            history = await conn.fetch(HISTORY_SQL, self.args.benchmark, ind)
            moves: List[Tuple[datetime.date, float]] = [
                (r["date"], float(r["offset"])) for r in history
            ]
            label = labels.get(ind, ind)
            industry_picks: List[BackfillPick] = []
            for side in (movers.SIDE_RISE, movers.SIDE_DROP):
                ranked = sorted(
                    self._side_moves(moves, side),
                    key=lambda m: (-m[1] if side == movers.SIDE_RISE
                                   else m[1]),
                )
                picked_dates: List[datetime.date] = []
                for d, off in ranked:
                    if len(picked_dates) >= self.args.per_side:
                        break
                    if any(abs((d - p).days) < self.args.min_date_gap_days
                           for p in picked_dates):
                        continue                     # same episode
                    if self._is_covered(ind, d, window, cov_days):
                        continue                     # hole already filled
                    question = movers.build_mover_question(label, side, d)
                    if (question, d) in stored_keys and not self.args.force:
                        continue                     # stored ask
                    known = artifact_asks.get((question, d))
                    if known is True and not self.args.force:
                        continue                     # artifact-recorded ask
                    if known is False and not self.args.retry_failed:
                        continue                     # failed ask stays done
                    picked_dates.append(d)
                    industry_picks.append(BackfillPick(
                        industry_id=ind, label=label, offset=off, side=side,
                        rank=len(picked_dates), question=question,
                        move_date=d))
            if industry_picks:
                logger.info("plan %s (%s): %d pick(s) — %s", ind, label,
                            len(industry_picks), "; ".join(
                                f"{p.side} {p.offset:+.2f} "
                                f"{p.move_date.isoformat()}"
                                for p in industry_picks))
            picks.extend(industry_picks)

        # Budget cut BEFORE the ask order sort: the biggest |offset| moves
        # populate first (the movers' cross-industry offset ranking — the
        # established comparability), chronologically within a magnitude
        # tie; the ask loop then walks oldest-first.
        picks = sorted(picks, key=lambda p: (-abs(p.offset), p.move_date))
        picks = picks[: self.args.max_asks]
        picks.sort(key=lambda p: p.move_date)        # oldest first
        logger.info("plan: %d due ask(s) (budget %d)",
                    len(picks), self.args.max_asks)
        return picks

    @staticmethod
    def _side_moves(
        moves: List[Tuple[datetime.date, float]], side: str,
    ) -> List[Tuple[datetime.date, float]]:
        """One side's history slice: rises (offset > 0) or drops (< 0)."""
        if side == movers.SIDE_RISE:
            return [(d, off) for d, off in moves if off > 0]
        return [(d, off) for d, off in moves if off < 0]

    async def _coverage_days(
        self, conn, industry_ids: List[str],
    ) -> Dict[str, Dict[datetime.date, int]]:
        """{industry_id: {day: n_articles}} for the flagged industries."""
        out: Dict[str, Dict[datetime.date, int]] = {}
        for r in await conn.fetch(COVERAGE_DAYS_SQL, industry_ids):
            out.setdefault(r["industry_id"], {})[
                r["date"]] = int(r["n_articles"])
        return out

    async def _stored_ask_keys(
        self, conn,
    ) -> Set[Tuple[str, datetime.date]]:
        """{(question, qa_day)} stored in llm.llm_qa for the movers category."""
        return {
            (r["question"], r["qa_day"])
            for r in await conn.fetch(ASKED_KEYS_SQL, movers.COOLDOWN_CATEGORY)
        }

    def _is_covered(
        self, industry_id: str, move_date: datetime.date,
        window: datetime.timedelta,
        cov_days: Dict[str, Dict[datetime.date, int]],
    ) -> bool:
        """True when the industry already has enough articles inside
        ``[move_date - window, move_date]`` (0 disables the skip)."""
        threshold = self.args.covered_skip_articles
        if threshold <= 0:
            return False
        days = cov_days.get(industry_id, {})
        n = sum(cnt for d, cnt in days.items()
                if move_date - window <= d <= move_date)
        return n >= threshold

    def _out_dir(self) -> Path:
        return Path(resolve_out_dir(
            str(Path(__file__).resolve()), OUTPUT_DIR_NAME,
            self.args.out_root))

    # ------------------------------------------------------------------
    # 3. Ask — the historical variant of the movers per-survivor ask
    # ------------------------------------------------------------------
    async def ask(
        self, picks: List[BackfillPick],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Search + summarize every due pick; one failure never kills the batch.

        Per pick: ``recency="noLimit"`` search (the provider's recency
        knob is relative to NOW — useless for a historical date), the
        date-anchored hit filter (``[move_date - window, move_date]`` —
        news dated AFTER the searched date never survives), then the
        compose summary over the survivors with ``anchor_date`` = the
        move date. A pick with no in-window survivors records a FAILED
        outcome.
        """
        provider = get_provider(self.args.provider)()
        opts = SearchOptions(
            count=self.args.count if self.args.count is not None
            else DAILY_COUNT,
            recency="noLimit", content_size="high")
        asked: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        for pick in picks:
            logger.info("ask [%s] %s", pick.side, pick.question)
            try:
                outcome = await self._ask_pick(provider, pick, opts=opts)
                asked.append(outcome)
                logger.info("  -> ok (%s)", ", ".join(
                    outcome["cited_refs"][:3]) or "no cites")
            except Exception as e:  # noqa: BLE001 — one failed ask moves on
                failed.append({**pick.to_dict(),
                               "error": f"{type(e).__name__}: {e}"})
                logger.warning("  -> FAILED %s: %s", type(e).__name__, e)
        return {"asked": asked, "failed": failed}

    async def _ask_pick(
        self, provider, pick: BackfillPick, *,
        opts: SearchOptions,
    ) -> Dict[str, Any]:
        """One ask: search -> date-anchored filter -> anchored compose."""
        move_date = pick.move_date
        resp = await provider.search(pick.question, opts)
        outcome = RefRecencyFilter(
            window_days=self.args.window_days, anchor=move_date).apply(
            resp.hits)
        if outcome.n_dropped:
            logger.info("  ref filter [move_date=%s, -%dd]: dropped %d/%d "
                        "hit(s) outside the window, %d survive",
                        move_date.isoformat(), self.args.window_days,
                        outcome.n_dropped, len(resp.hits), len(outcome.hits))
        if not outcome.hits:
            raise OnlineSearchError(
                f"no in-window references for {pick.question!r} "
                f"({len(resp.hits)} raw, window "
                f"[{move_date - datetime.timedelta(days=self.args.window_days)}"
                f" .. {move_date.isoformat()}])")
        summary = await provider.summarize_hits_via_compose(
            pick.question, outcome.hits, model=self.args.model,
            lang=self.args.lang, anchor_date=move_date)
        return {
            **pick.to_dict(),
            "answer": summary.answer,
            "cited_refs": summary.cited_refs,
            "n_refs": len(summary.hits),
            # The ask's FULL in-window hit list — the movers artifact
            # contract (builds.text resolves these into text.news rows +
            # llm.llm_qa provenance; the filter already bounded every
            # known date by the move date).
            "references": [h.to_dict() for h in summary.hits],
            "provider": summary.provider, "model": summary.model,
        }

    # ------------------------------------------------------------------
    # 4. Write — merged movers-shaped envelopes, one per move date
    # ------------------------------------------------------------------
    def write_artifacts(
        self, outcomes: Dict[str, List[Dict[str, Any]]],
        study: CoverageStudy,
    ) -> List[str]:
        """Merge the run's asks into the per-date backfill envelopes.

        One ``ai_daily_<date>_backfill_movers.json`` per move date: the
        standalone movers envelope shape the ``builds.text`` loader
        reads (``plan_date`` dates every ask's rows;
        ``asked[].industry_id`` pre-sets the scope). Re-running appends:
        existing asked/failed entries with the same question are
        REPLACED by the fresh copy (last-wins, the loader's own dedupe
        direction) — the envelope never accumulates duplicates.
        """
        out_dir = self._out_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        by_date: Dict[datetime.date, Dict[str, List[Dict[str, Any]]]] = {}
        for bucket in ("asked", "failed"):
            for entry in outcomes[bucket]:
                move_date = _entry_ask_date(entry)
                slots = by_date.setdefault(
                    move_date, {"asked": [], "failed": []})
                slots[bucket].append(entry)

        written: List[str] = []
        for move_date, fresh in sorted(by_date.items()):
            path = backfill_artifact_path(out_dir, move_date)
            env: Dict[str, Any] = {
                "status": "ok",                 # the envelope is the record
                "phase": "coverage_backfill",
                "benchmark": study.benchmark,
                "plan_date": move_date.strftime("%Y-%m-%d"),
                "candidates": {"rises": [], "drops": []},
                "asked": [], "failed": [],
            }
            if path.exists() and path.is_file():
                try:
                    with path.open("r", encoding="utf-8") as f:
                        existing = json.load(f)
                    if isinstance(existing, dict):
                        env["candidates"] = existing.get("candidates") \
                            or env["candidates"]
                        env["asked"] = existing.get("asked") or []
                        env["failed"] = existing.get("failed") or []
                except (OSError, ValueError):
                    logger.warning("backfill: unreadable %s — starting a "
                                   "fresh envelope", path.name)
            for bucket in ("asked", "failed"):
                fresh_questions = {e["question"] for e in fresh[bucket]}
                kept = [e for e in env[bucket]
                        if e.get("question") not in fresh_questions]
                env[bucket] = kept + fresh[bucket]
            with path.open("w", encoding="utf-8") as f:
                json.dump(env, f, ensure_ascii=False, indent=2)
            written.append(path.name)
            logger.info("saved %s (%d asked, %d failed cumulative)",
                        path.name, len(env["asked"]), len(env["failed"]))
        return written


if __name__ == "__main__":
    CoverageBackfill().execute()
