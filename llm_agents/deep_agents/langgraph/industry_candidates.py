"""llm_agents.deep_agents.langgraph.industry_candidates — the
INDUSTRY VOCABULARY + keyword prefilter behind the preview text
classification's industry-attempt read (the jev ``content_industry``
template's candidate list).

The vocabulary is the repo's OWN: the DISTINCT ``stats.industry_identity``
names (106 today), read through the SQL-first template
``database/sql_views/llm_agents/industry/industry_names.sql`` — the
labels a classification produces are always names the analysis schema
itself keys on, never a hardcoded list drifting from the data.

The PREFILTER is the jev ceiling handler: the /v1/systemone contract
accepts ≤26 options per choice question and 106 names would also be
unreadable — so the candidates for one read are the names whose tokens
KEYWORD-HIT the text head (CJK names match by substring, latin names
by case-folded word boundary), longest names first (a substring hit on
a longer compound beats its fragment). Zero hits → empty list (the
engine skips the question; no candidates is an honest answer). Fail-soft
by contract: a DB error degrades to the empty in-memory cache and an
empty candidate list — the classification is enrichment, never a seam
breaker.
"""
from __future__ import annotations

import logging
import re
import time
from typing import List, Optional

logger = logging.getLogger("deep_agents.industry_candidates")

# The jev per-question option ceiling (the /v1/systemone contract).
MAX_CANDIDATES = 26
# The vocabulary TTL — the industry identity table moves at the daily
# build cadence; minutes are plenty.
VOCAB_CACHE_TTL_SEC = 900.0

_WORD_RE = re.compile(r"[a-zA-Z]+")


class IndustryCandidateResolver:
    """The TTL-cached vocabulary + the keyword prefilter (share the
    module ``RESOLVER`` singleton)."""

    def __init__(self, *,
                 cache_ttl_sec: float = VOCAB_CACHE_TTL_SEC) -> None:
        self._cache_ttl_sec = cache_ttl_sec
        self._names: Optional[List[str]] = None
        self._loaded_at: float = 0.0

    # ------------------------------------------------------------ read
    def candidates_for(self, text_head: str,
                       *, max_candidates: int = MAX_CANDIDATES,
                       ) -> List[str]:
        """The keyword-hit industry names for ONE text head (≤26,
        longest first). Sync + fail-soft: the DB fetch runs inline (a
        ~0.1s bounded read on the TTL miss only) and ANY failure
        returns whatever is cached — possibly nothing."""
        names = self._vocabulary()
        if not names or not text_head:
            return []
        return self._prefilter(names, text_head, max_candidates)

    async def candidates_for_async(self, text_head: str,
                                   *, max_candidates: int = MAX_CANDIDATES,
                                   ) -> List[str]:
        """The async face for the loop seam (the DB fetch off the event
        loop)."""
        import asyncio
        return await asyncio.to_thread(
            self.candidates_for, text_head, max_candidates=max_candidates)

    # ------------------------------------------------------- internals
    def _vocabulary(self) -> List[str]:
        if (self._names is not None
                and time.monotonic() - self._loaded_at < self._cache_ttl_sec):
            return self._names
        try:
            self._names = self._fetch_names()
        except Exception as exc:  # noqa: BLE001 — fail-soft enrichment
            logger.warning("industry vocabulary read failed: %s", exc)
            self._names = self._names or []
        self._loaded_at = time.monotonic()
        return self._names

    @staticmethod
    def _fetch_names() -> List[str]:
        from _common.sql_views import render_sql_view
        from _common.db_commons import execute

        view = render_sql_view("llm_agents/industry/industry_names")
        rows = execute(view.sql, view.params, fetch=True) or []
        return [str(r[0]).strip() for r in rows
                if str(r[0] or "").strip()]

    @staticmethod
    def _prefilter(names: List[str], text_head: str,
                   max_candidates: int) -> List[str]:
        folded = text_head.casefold()
        words = set(_WORD_RE.findall(folded))
        hits: List[str] = []
        for name in sorted(names, key=len, reverse=True):
            probe = name.casefold()
            if any(ch.isascii() and ch.isalpha() for ch in probe):
                # latin name: word-boundary match (TMT must not hit
                # every text containing the letter t)
                tokens = _WORD_RE.findall(probe)
                if tokens and all(t in words for t in tokens):
                    hits.append(name)
            elif probe in folded:
                hits.append(name)
            if len(hits) >= max_candidates:
                break
        return hits


RESOLVER = IndustryCandidateResolver()
