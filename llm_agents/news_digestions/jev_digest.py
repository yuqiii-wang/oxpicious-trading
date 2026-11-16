"""llm_agents.news_digestions.jev_digest — Jev (System One) digestion.

The generative digestion (``agent.py``) spends a provider chat call per
article to write the summary AND the sentiment. The Jev path answers the
SENTIMENT with the resident local decision model instead
(``POST /v1/systemone``, model ``onejev-1.6-4b`` — ~0.1 s, local GPU,
zero provider spend, deterministic): three typed questions over a compact
fact state (title/source/industry/word_count, per the jev skill's
"state carries compact facts, not prose"):

  * ``bullish``   — noul  "该新闻对相关行业构成利好"   → P(利好)
  * ``bearish``   — noul  "该新闻对相关行业构成利空"   → P(利空)
  * ``intensity`` — score ["忽略", "轻微", "明显", "重大"] → expected 0..3

The two nouls follow the jev skill's measured rule (yes/no is this
model's strong suit; k-way choices read position-biased), so the
direction is the DIFFERENCE of two independent reads and the magnitude
is scaled by the expected intensity:

  sentiment_level = (P(利好) − P(利空)) · 5 · (0.35 + 0.65 · intensity/3)

A weak-impact read therefore lands near 0 even when the direction is
clear — a ±5 on an 忽略-intensity headline would overstate it. The
``summary`` column is NOT NULL, and jev is NOT a prose model, so the
summary is a deterministic template carrying the calibrated numbers
(direction, both probabilities, intensity label, the title) — honest
about what the row contains. ``llm_model`` records ``onejev-1.6-4b``.

  * ``JevDigestionEngine``       — one article → one store-shaped row
    (async; the sanctioned sync client rides ``asyncio.to_thread``).
  * ``digest_new_articles_jev``  — the builds.text side task: digest the
    articles a news build just wrote and upsert their digestion rows
    (fail-soft — a host outage logs a warning, never fails the build).
    The fake-news detector (``fake_news.py``) rides EVERY row here too,
    same as the CLI loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from llm_agents.news_digestions.agent import truncate_content
from llm_agents.news_digestions.fake_news import FakeNewsDetector
from llm_agents.news_digestions.store import fetch_pending_articles, upsert_digestions

logger = logging.getLogger(__name__)

JEV_MODEL = "onejev-1.6-4b"

INTENSITY_LEVELS = ["忽略", "轻微", "明显", "重大"]
# Magnitude floor: a confident direction on an 忽略-intensity read still
# lands near ±1.75 instead of ±5 (see module docstring).
INTENSITY_FLOOR = 0.35
MAX_CONTENT_CHARS = 4000  # jev reads a fact state — no long prose needed


class JevDigestionConfig(BaseModel):
    """Typed knobs of the jev digestion read."""

    model: str = Field(default=JEV_MODEL, description="systemone model id")
    max_content_chars: int = Field(default=MAX_CONTENT_CHARS,
                                   ge=200, description="Title/body head the "
                                   "state's `headline` field may carry")
    intensity_floor: float = Field(default=INTENSITY_FLOOR, ge=0.0, le=1.0)
    read_timeout_s: float = Field(default=20.0, gt=0.0,
                                  description="Per-request read timeout "
                                  "(keep SHORT inside pipeline runs)")


class JevDigestionEngine:
    """One text.news article → one text.news_digestions row via jev."""

    def __init__(self, config: Optional[JevDigestionConfig] = None):
        self.config = config or JevDigestionConfig()

    # -- the read ---------------------------------------------------------

    def _build_state(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Compact fact state — typed fields beat a paragraph (jev skill)."""
        date = article["date"]
        return {
            "task": "评估这篇新闻对相关行业的情绪影响",
            "title": truncate_content(article.get("title"),
                                      self.config.max_content_chars // 2),
            "headline": truncate_content(article.get("content"),
                                         self.config.max_content_chars),
            "source": article.get("source") or "",
            "industry": article.get("industry_id") or "",
            "date": date.isoformat() if hasattr(date, "isoformat") else str(date),
            "word_count": int(article.get("word_count") or 0),
        }

    def _questions(self) -> Dict[str, Dict[str, Any]]:
        return {
            "bullish": {"type": "noul",
                        "instructions": "该新闻对相关行业构成利好"},
            "bearish": {"type": "noul",
                        "instructions": "该新闻对相关行业构成利空"},
            "intensity": {"type": "score",
                          "instructions": "该新闻对相关行业的影响强度",
                          "criteria": list(INTENSITY_LEVELS)},
        }

    def _read_sync(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """The blocking systemone call (client side validates the contract;
        any breach raises LocalModelHostError like any other failure)."""
        from server_cluster.llm.client import LocalModelHostClient

        response = LocalModelHostClient().systemone(
            state=state, questions=self._questions(),
            read_timeout_s=self.config.read_timeout_s)
        return {key: answer.model_dump()
                for key, answer in response.answers.items()}

    # -- article → row -----------------------------------------------------

    async def digest_article(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Digest one fetched text.news row into a store-shaped dict.

        Raises LocalModelHostError on transport/contract failure — the
        caller decides the fail-soft policy (the side task logs and
        continues; the CLI counts the article as failed).
        """
        answers = await asyncio.to_thread(
            self._read_sync, self._build_state(article))
        p_bull: float = answers["bullish"]["noul"]
        p_bear: float = answers["bearish"]["noul"]
        score: float = answers["intensity"]["score"]
        intensity_norm = min(max(score / (len(INTENSITY_LEVELS) - 1), 0.0), 1.0)
        level = round((p_bull - p_bear) * 5.0 * (
            self.config.intensity_floor
            + (1.0 - self.config.intensity_floor) * intensity_norm), 2)
        level = max(-5.0, min(5.0, level))

        if level > 0.5:
            direction = "偏利好"
        elif level < -0.5:
            direction = "偏利空"
        else:
            direction = "中性"
        intensity_label = INTENSITY_LEVELS[
            min(int(round(score)), len(INTENSITY_LEVELS) - 1)]
        summary = (f"Jev 情绪判定：{direction}"
                   f"（P(利好)={p_bull:.2f}，P(利空)={p_bear:.2f}，"
                   f"影响强度={intensity_label}）。"
                   f"{str(article.get('title') or '')[:120]}")
        return {
            "news_id": article["news_id"],
            "date": article["date"],
            "industry_id": article.get("industry_id"),
            "summary": summary,
            "sentiment_level": level,
            "llm_model": self.config.model,
        }


async def digest_new_articles_jev(
    conn,
    news_ids: Sequence[int],
    *,
    concurrency: int = 4,
    config: Optional[JevDigestionConfig] = None,
) -> Dict[str, int]:
    """builds.text side task: jev-digest the given news_ids.

    Fetches the full article rows still missing a digestion row
    (idempotent — an article digested by an earlier run is skipped),
    runs the engine under a small semaphore and upserts each row as it
    completes. Returns {digested, failed, pending}. Raises nothing
    except on a dead DB connection — a jev failure is per-article.
    """
    articles = await fetch_pending_articles(conn, news_ids=list(news_ids))
    if not articles:
        return {"digested": 0, "failed": 0, "pending": 0}
    engine = JevDigestionEngine(config)
    detector = FakeNewsDetector()
    sem = asyncio.Semaphore(max(1, concurrency))
    ok = failed = 0

    async def run_one(article: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        async with sem:
            try:
                return await detector.annotate_row(
                    await engine.digest_article(article), article)
            except Exception as exc:  # one bad read never kills the build
                failed_article = article["news_id"]
                logger.warning("    [jev-digest] news_id=%s failed: %s: %s",
                               failed_article, type(exc).__name__, exc)
                return None

    rows = await asyncio.gather(*(run_one(a) for a in articles))
    for row in rows:
        if row is None:
            continue
        await upsert_digestions(conn, [row])
        ok += 1
    pending = await fetch_pending_articles(conn, news_ids=list(news_ids))
    return {"digested": ok, "failed": len(articles) - ok, "pending": len(pending)}
