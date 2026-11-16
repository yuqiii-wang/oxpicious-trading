"""llm_agents.news_digestions.fake_news — the fake-news detector slot.

The sentiment read (``agent.py`` chat / ``jev_digest.py`` jev) answers
"how does this article move the market"; this module answers "can this
article be trusted at all". It runs alongside the sentiment read for
EVERY digested article — both per-article call sites (the CLI ``run``
loop and the builds.text jev side task) merge its verdict into the row
before the store upsert, so ``text.news_digestions.is_fake_news``
(BOOLEAN NOT NULL DEFAULT false) is always detector-written, never left
to the column default by accident.

The detection logic is deliberately EMPTY for now:
``FakeNewsDetector.detect_article`` returns False until a real check is
wired in (source credibility heuristics, cross-source corroboration, or
an LLM audit pass — a sibling of the sentiment engines). The plumbing
is already live, so the real logic lands as one method body:

  * ``FakeNewsDetector``         — one article -> one bool verdict;
    ``annotate_row`` merges it into a store-shaped digestion row
    fail-soft (a detector error logs and records False, never failing
    the digestion that earned the row).
  * ``FakeNewsConfig``           — the typed knobs (``enabled`` lets a
    caller skip the pass without unwiring the call sites).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class FakeNewsConfig(BaseModel):
    """Typed knobs of the fake-news detector pass."""

    enabled: bool = Field(
        default=True,
        description="Run the detector on every digested article; False "
                    "records the column default (False) instead.")


class FakeNewsDetector:
    """One text.news article -> one is_fake_news verdict (bool)."""

    def __init__(self, config: Optional[FakeNewsConfig] = None):
        self.config = config or FakeNewsConfig()

    # -- the read ---------------------------------------------------------

    async def detect_article(self, article: Dict[str, Any]) -> bool:
        """True when the article should be flagged as fake news.

        EMPTY for now — every article passes. The real detection pass
        (TODO) lands here; keep it content-only like the sentiment
        engines (title/source/industry/content in, verdict out) and
        raise on failure — ``annotate_row`` owns the fail-soft policy.
        """
        if not self.config.enabled:
            return False
        # TODO(fake-news): the real detection pass — for now nothing is
        # flagged (the column default false is the honest verdict).
        return False

    # -- article + row -> row ----------------------------------------------

    async def annotate_row(self,
                           row: Dict[str, Any],
                           article: Dict[str, Any]) -> Dict[str, Any]:
        """Merge the verdict into a digestion row, fail-soft.

        A detector error logs a warning and records False — the row the
        sentiment engine earned is still written (the sentiment engines'
        own failures are what fail an article, not ours).
        """
        try:
            row["is_fake_news"] = await self.detect_article(article)
        except Exception as exc:  # one bad detector call never kills a row
            logger.warning("    [fake-news] news_id=%s failed: %s: %s",
                           article.get("news_id"), type(exc).__name__, exc)
            row["is_fake_news"] = False
        return row
