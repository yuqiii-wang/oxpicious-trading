"""llm_agents.news_digestions.fake_news — the fake-news detector.

The sentiment read (``agent.py`` chat / ``jev_digest.py`` jev) answers
"how does this article move the market"; this module answers "what kind
of text is this, and can it be trusted". It runs alongside the sentiment
read for EVERY digested article — both per-article call sites (the CLI
``run`` loop and the builds.text jev side task) merge its verdict into
the row before the store upsert, so ``text.news_digestions.category``
and ``is_fake_news`` are always detector-written, never left to the
column defaults by accident.

Two lanes, both Jev (System One) reads over compact fact states
(``POST /v1/systemone`` — noul questions, the model's strong suit per
the jev skill; source + title + content head ride the state):

  * CLASSIFY — the discourse category (``category``, label_dim domain
    ``news_category``): one request, four noul reads (discussion /
    analysis / opinion / statement) + one asks-tomorrow read. The
    category is the ARGMAX of the four; a low-confidence read lands 99
    other. What kind of text an item is decides what "fake" can even
    mean — most zhihu items are discussions (questions soliciting
    views), while some are personal statements (an explicit 涨/跌 call).
  * THE DISINGENUOUS TOMORROW-QUESTION — the fake verdict
    (``is_fake_news``): the title asks about the next day's move
    (明天怎么走？) while earlier items of the same ORIGIN (author when
    captured, else source) within the lookback window already made
    EXPLICIT directional statements — the question is engagement bait,
    not a genuine ask. Opinions/sentiments never trigger it: the prior
    read is a separate noul that separates 明确断言 (statement) from
    仅表达观点 (opinion), so only statements count as evidence. The
    prior pass runs ONLY for asking titles (one bounded request per
    prior, newest first, stop at the first hit).

  * ``FakeNewsConfig``   — the typed knobs (thresholds follow the jev
    skill's measured calibration: obvious propositions read ~0.8,
    noise ~0.5 — 0.60 gates the two verdict reads, 0.45 the argmax).
  * ``FakeNewsVerdict``  — the typed per-article outcome crossing to
    the row merge.
  * ``FakeNewsDetector`` — one article (+ the prior fetch connection)
    -> one verdict; ``annotate_row`` merges it into a store-shaped
    digestion row fail-soft (a detector error logs and records the
    defaults, never failing the digestion that earned the row).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from _common.labels import NEWS_CATEGORY_CODE

from llm_agents.news_digestions.agent import truncate_content
from llm_agents.news_digestions.store import fetch_prior_articles

logger = logging.getLogger(__name__)

# Category codes the classify lane argmaxes over (99 other = low
# confidence — the fallback, never a jev read of its own).
_CATEGORY_QUESTION_CODES = (
    ("discussion", NEWS_CATEGORY_CODE["discussion"]),
    ("analysis", NEWS_CATEGORY_CODE["analysis"]),
    ("opinion", NEWS_CATEGORY_CODE["opinion"]),
    ("statement", NEWS_CATEGORY_CODE["statement"]),
)


class FakeNewsConfig(BaseModel):
    """Typed knobs of the fake-news detector pass."""

    enabled: bool = Field(
        default=True,
        description="Run the detector on every digested article; False "
                    "records the column defaults (99 other / False) "
                    "instead.")
    lookback_days: int = Field(
        default=14, ge=1,
        description="How many days back the prior-statement hunt reads "
                    "the same origin's earlier items.")
    max_priors: int = Field(
        default=10, ge=1,
        description="Upper bound of prior items read per asking title "
                    "(newest first, stops at the first statement hit).")
    min_confidence: float = Field(
        default=0.60,
        description="Noul gate for the two verdict reads (asks-"
                    "tomorrow, prior explicit statement) — the jev "
                    "skill's noise floor sits near 0.5, obvious "
                    "propositions near 0.8.")
    category_min_confidence: float = Field(
        default=0.45,
        description="Argmax gate for the category read; below it the "
                    "article lands 99 other.")
    max_state_chars: int = Field(
        default=2000, ge=200,
        description="Content head the prior/classify states may carry.")
    read_timeout_s: float = Field(
        default=20.0, gt=0.0,
        description="Per-request systemone read timeout (keep SHORT "
                    "inside pipeline runs).")


class FakeNewsVerdict(BaseModel):
    """The typed per-article detector outcome."""

    category: int = Field(
        description="news_category label code (99 other = the "
                    "low-confidence fallback).")
    is_fake_news: bool = Field(
        description="The disingenuous tomorrow-question verdict.")
    asks_tomorrow: bool = Field(
        description="The title asks about the next day's move (the "
                    "verdict's trigger read).")
    prior_news_id: Optional[int] = Field(
        default=None,
        description="The prior item whose explicit statement flagged "
                    "the asking title (None when not fake).")


class FakeNewsDetector:
    """One text.news article -> one FakeNewsVerdict (two jev lanes)."""

    def __init__(self, config: Optional[FakeNewsConfig] = None):
        self.config = config or FakeNewsConfig()
        # Serializes the caller's DB connection around the detector's own
        # conn reads (the prior hunt): the digestion call sites share ONE
        # asyncpg connection across the semaphore's concurrent tasks, and
        # asyncpg rejects overlapping operations on one connection —
        # without the lock two simultaneous prior hunts would raise
        # InterfaceError and silently land the fail-soft defaults.
        self.db_lock = asyncio.Lock()

    # -- the jev reads -----------------------------------------------------

    def _build_state(self, article: Dict[str, Any], task: str
                     ) -> Dict[str, Any]:
        """Compact fact state — typed fields beat a paragraph (jev skill)."""
        date = article["date"]
        return {
            "task": task,
            "title": truncate_content(article.get("title"),
                                      self.config.max_state_chars // 2),
            "headline": truncate_content(article.get("content"),
                                         self.config.max_state_chars),
            "source": article.get("source") or "",
            "author": article.get("author") or "",
            "date": date.isoformat() if hasattr(date, "isoformat") else str(date),
            "word_count": int(article.get("word_count") or 0),
        }

    def _classify_questions(self) -> Dict[str, Dict[str, Any]]:
        """The category + asks-tomorrow batch — one request, five nouls."""
        return {
            "discussion": {"type": "noul",
                           "instructions": "该文主要在提问或发起讨论（如询问明天走势、征集观点），而非给出判断"},
            "analysis": {"type": "noul",
                         "instructions": "该文是基于数据与逻辑推演的行情分析"},
            "opinion": {"type": "noul",
                        "instructions": "该文仅表达作者个人的主观观点或情绪倾向，并未明确断言涨跌"},
            "statement": {"type": "noul",
                          "instructions": "该文明确断言了市场接下来会上涨或下跌（如：明天涨、明天必跌），给出了确定的方向判断"},
            "asks_tomorrow": {"type": "noul",
                              "instructions": "该文标题以疑问句询问明天或接下来的走势（如：明天怎么走？明天会涨吗、会跌吗？），标题本身没有给出涨跌判断"},
        }

    def _prior_questions(self) -> Dict[str, Dict[str, Any]]:
        return {
            "explicit_statement": {"type": "noul",
                                   "instructions": "该文对次日或短期市场走势给出了明确的涨跌断言（如：明天涨、明天必跌、要回调），而不是仅表达观点、情绪或罗列分析"},
        }

    def _read_sync(self, state: Dict[str, Any],
                   questions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """The blocking systemone call (the client validates the contract;
        any breach raises LocalModelHostError like any other failure)."""
        from server_cluster.llm.client import LocalModelHostClient

        response = LocalModelHostClient().systemone(
            state=state, questions=questions,
            read_timeout_s=self.config.read_timeout_s)
        return {key: answer.model_dump()
                for key, answer in response.answers.items()}

    async def _read(self, state: Dict[str, Any],
                    questions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        return await asyncio.to_thread(self._read_sync, state, questions)

    # -- the verdict --------------------------------------------------------

    async def detect_article(self, article: Dict[str, Any],
                             conn=None) -> FakeNewsVerdict:
        """Classify the article, then run the disingenuous-question pass.

        ``conn`` (asyncpg) unlocks the prior hunt: asking titles get the
        same origin's earlier items fetched (author when captured, else
        source) and read for explicit directional statements. Raises on
        jev/DB failure — ``annotate_row`` owns the fail-soft policy.
        """
        if not self.config.enabled:
            return FakeNewsVerdict(category=NEWS_CATEGORY_CODE["other"],
                                   is_fake_news=False, asks_tomorrow=False)

        answers = await self._read(
            self._build_state(article, "判断这篇新闻的文本类别"),
            self._classify_questions())
        category, best = NEWS_CATEGORY_CODE["other"], 0.0
        for key, code in _CATEGORY_QUESTION_CODES:
            p = float(answers[key]["noul"])
            if p > best:
                category, best = code, p
        if best < self.config.category_min_confidence:
            category = NEWS_CATEGORY_CODE["other"]
        asks_tomorrow = (float(answers["asks_tomorrow"]["noul"])
                         >= self.config.min_confidence)

        if not asks_tomorrow or conn is None:
            return FakeNewsVerdict(category=category, is_fake_news=False,
                                   asks_tomorrow=asks_tomorrow)

        # The prior fetch is the detector's only conn use — hold db_lock so
        # concurrent articles sharing one asyncpg connection never overlap.
        async with self.db_lock:
            priors = await fetch_prior_articles(
                conn, article,
                lookback_days=self.config.lookback_days,
                limit=self.config.max_priors)
        for prior in priors:
            prior_answers = await self._read(
                self._build_state(prior, "判断这篇早前新闻是否明确断言了走势方向"),
                self._prior_questions())
            if (float(prior_answers["explicit_statement"]["noul"])
                    >= self.config.min_confidence):
                return FakeNewsVerdict(
                    category=category, is_fake_news=True,
                    asks_tomorrow=True, prior_news_id=prior["news_id"])
        return FakeNewsVerdict(category=category, is_fake_news=False,
                               asks_tomorrow=True)

    # -- article + row -> row ----------------------------------------------

    async def annotate_row(self,
                           row: Dict[str, Any],
                           article: Dict[str, Any],
                           conn=None) -> Dict[str, Any]:
        """Merge the verdict into a digestion row, fail-soft.

        A detector error logs a warning and records the defaults (99
        other / False) — the row the sentiment engine earned is still
        written (the sentiment engines' own failures are what fail an
        article, not ours).
        """
        try:
            verdict = await self.detect_article(article, conn=conn)
            row["category"] = verdict.category
            row["is_fake_news"] = verdict.is_fake_news
            if verdict.is_fake_news:
                logger.info(
                    "    [fake-news] news_id=%s asking title + prior "
                    "statement news_id=%s — flagged",
                    article.get("news_id"), verdict.prior_news_id)
        except Exception as exc:  # one bad detector call never kills a row
            logger.warning("    [fake-news] news_id=%s failed: %s: %s",
                           article.get("news_id"), type(exc).__name__, exc)
            row["category"] = NEWS_CATEGORY_CODE["other"]
            row["is_fake_news"] = False
        return row
