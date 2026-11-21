"""builds.text.loaders.ai_daily — AI daily market summaries.

Parses the ``downloads.macro.ai_daily`` artifacts under
``temps/ai_daily/``: the general ``ai_daily_<date>.json`` envelopes (AI
answer + search references + embedded ``movers`` and ``geo`` asks) and
the standalone ``*_movers.json`` envelopes.
"""
from __future__ import annotations

import asyncio
import datetime
from concurrent.futures import ThreadPoolExecutor
import glob
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from builds.text import paths
from builds.text.loaders._parsing import parse_date_str
from llm_agents.online_search_summary.storage.matching import date_from_url

logger = logging.getLogger(__name__)

SOURCE = "ai_daily"

MOVERS_SUFFIX = "_movers.json"

# text.news PK is (title, source, date) btree — a title whose utf-8 bytes
# push the index row past the ~2704-byte page limit fails the WHOLE
# builds.text upsert (asyncpg ProgramLimitExceededError, 2026-11-20: a
# 10k-char web hit). Search hits sometimes carry whole flattened
# frontmatter blobs ("---title: …source: …datetime: …") instead of a
# title, so: extract the embedded title when the blob shape is there,
# then hard-clamp whatever remains.
_TITLE_BLOB_PREFIX = "---title:"
_TITLE_BLOB_KEY = re.compile(r"\s*(?:source|datetime|date|url)\s*:", re.I)
MAX_TITLE_CHARS = 500  # ~1.5 KB utf-8 worst case — safely under the limit


def sanitize_hit_title(raw: str) -> str:
    """A PK-safe title for a search-reference hit.

    A flattened-frontmatter blob (newlines stripped by the search
    provider) yields its embedded ``title:`` value — the text up to the
    next ``source:``/``datetime:``-style key; anything else passes
    through, and the result is clamped to :data:`MAX_TITLE_CHARS`.
    """
    title = raw.strip()
    if title.lower().startswith(_TITLE_BLOB_PREFIX):
        embedded = title[len(_TITLE_BLOB_PREFIX):].lstrip(": ")
        m = _TITLE_BLOB_KEY.search(embedded)
        if m:
            embedded = embedded[:m.start()].rstrip(" -—|")
        title = embedded.strip() or title
    return title[:MAX_TITLE_CHARS]


class HitTitleSummarizer:
    """LLM headline for over-long search-hit titles (the precheck upgrade).

    A hit title past :data:`MAX_TITLE_CHARS` is a digest blob, not a
    headline: the hard clamp keeps the text.news PK safe but stores a
    chopped fragment. This summarizer asks the chat provider for a real
    concise headline instead; ANY failure (provider error, timeout,
    empty/oversized reply) yields ``""`` and the caller falls back to
    the clamp — the build must never fail because a title could not be
    summarized. Successful summaries are cached per raw title so the
    loader and ``ai_daily_qa`` resolve byte-identical natural keys.
    """

    # One-shot headline ask: temperature 0 for determinism, the shared
    # default model family (the secrets registry's primary). The token
    # ceiling must leave room for the model's REASONING tokens before
    # the visible content — glm-5.3-flash reasoning ate a 200 cap
    # whole (2026-11-20: five empty replies) and the answer never
    # surfaced; 1024 keeps the headline well-funded.
    TARGET_CHARS = 50
    MAX_TOKENS = 1024

    def __init__(self) -> None:
        self._cache: Dict[str, str] = {}

    def needs_summarize(self, title: str) -> bool:
        """True when the title is past the clamp threshold — the rows
        the hard clamp would truncate are exactly the ones worth an LLM
        headline."""
        return len(title) > MAX_TITLE_CHARS

    def summarize(self, raw_title: str) -> str:
        """The LLM headline (sanitized + clamped), or ``""`` on any
        failure — the caller falls back to :func:`sanitize_hit_title`."""
        cached = self._cache.get(raw_title)
        if cached is not None:
            return cached
        try:
            # The load phase runs INSIDE the pipeline's event loop
            # (TextBuild.execute → asyncio.run(amain())), so the
            # coroutine rides a fresh loop on a worker thread —
            # asyncio.run() on THIS thread would raise
            # "cannot be called from a running event loop".
            with ThreadPoolExecutor(max_workers=1) as pool:
                content = pool.submit(
                    lambda: asyncio.run(self._ask(raw_title))).result()
        except Exception as exc:  # noqa: BLE001 — fail-soft by contract
            logger.warning("    [title-summarize] LLM request failed, "
                           "falling back to the hard clamp: %s", exc)
            return ""
        title = sanitize_hit_title(content or "")
        if not title:
            logger.warning("    [title-summarize] empty LLM reply, "
                           "falling back to the hard clamp")
            return ""
        self._cache[raw_title] = title
        return title

    async def _ask(self, raw_title: str) -> str:
        """One chat completion: the blob in, a headline out.

        The MODEL comes from the active ``static.secret_config`` llm
        row — the general primary model (glm-5.3-flash now) — via
        ``LlmRuntimeConfig.load()``; the provider is built by the
        label→class factory so zhipu and the dummy test double both
        serve, and a missing/failed row degrades to the provider's
        built-ins. Lazy imports — the loader must not hard-depend on
        the deep-agent stack at import time."""
        from llm_agents.deep_agents.llm_config import (
            LlmRuntimeConfig,
            build_chat_provider,
        )

        config = await LlmRuntimeConfig.load()
        provider = build_chat_provider(
            config.provider if config else "zhipu",
            api_key=config.api_key if config else None,
            api_url=config.api_url if config else None,
        )
        messages = [
            {"role": "system",
             "content": ("你是新闻编辑。把给定的超长新闻标题/摘要文本压缩成一条"
                         f"简洁的中文新闻标题，不超过{self.TARGET_CHARS}个字。"
                         "只返回标题本身，不要任何解释、引号或前后缀。")},
            {"role": "user",
             "content": raw_title[:4000]},
        ]
        reply = await provider.chat_complete(
            messages,
            model=(config.model_name if config else None) or None,
            temperature=0.0, max_tokens=self.MAX_TOKENS)
        return str(reply.get("content") or "").strip()


# Module-level shared instance: the loader's hit_news_row and
# ai_daily_qa's _hit resolve through the SAME cache, so a summarized
# title lands byte-identical in the text.news natural key and the
# llm_qa ref key.
_TITLE_SUMMARIZER = HitTitleSummarizer()


def resolve_hit_title(raw: str) -> str:
    """The title a hit carries into the text.news natural key.

    The single resolution point for BOTH writers (the loader's
    hit_news_row and ai_daily_qa's _hit — their keys must stay
    byte-identical): over-long titles go through the LLM summarizer,
    anything else (and any LLM failure) through the hard clamp.
    """
    title = (raw or "").strip()
    if _TITLE_SUMMARIZER.needs_summarize(title):
        summarized = _TITLE_SUMMARIZER.summarize(title)
        if summarized:
            return summarized
    return sanitize_hit_title(title)


def _ai_daily_file_date(path: str) -> Optional[datetime.date]:
    """The biz date embedded in an artifact name (ai_daily_YYYY-MM-DD[…])."""
    m = re.search(r"ai_daily_(\d{4}-\d{2}-\d{2})", os.path.basename(path))
    return datetime.date.fromisoformat(m.group(1)) if m else None


def hit_news_row(
    hit: Dict[str, Any],
    fallback_date: datetime.date,
) -> Optional[Dict[str, Any]]:
    """The text.news row for one search-reference hit.

    Shared by :func:`load_ai_daily` and builds.text.ai_daily_qa — the Q&A
    refs resolve to news_ids through the SAME (title, source, date) natural
    key this function writes, so every hit row written here is resolvable.
    Dated by publish_date_raw, else the URL-embedded date, else the
    envelope's own date as last resort (the same shape the --store fast
    path wrote: source = media or 'web').
    """
    title = resolve_hit_title(hit.get("title") or "")
    if not title:
        return None
    return {
        "title": title,
        "content": (hit.get("content") or "").strip() or None,
        "date": (parse_date_str(hit.get("publish_date_raw"))
                 or date_from_url((hit.get("link") or "").strip())
                 or fallback_date),
        "source": (hit.get("media") or "").strip() or "web",
        "url": (hit.get("link") or "").strip() or None,
        "author": None,
    }


def iter_ai_daily_envelopes(
) -> List[Tuple[str, Dict[str, Any], bool, datetime.date]]:
    """Parse every artifact; return (path, data, is_movers_file, file_date).

    Standalone ``*_movers.json`` envelopes come FIRST, general artifacts
    after (both filename-sorted): the same question re-asked by a later run
    re-embeds a fresher copy that must win last-wins dedupe. *file_date* is
    the artifact's own data date — target_date, else the movers plan_date,
    else the filename's embedded date.
    """
    general_files = [
        p for p in sorted(glob.glob(
            os.path.join(paths.AI_DAILY_DIR, "ai_daily_*.json")))
        if not p.endswith(MOVERS_SUFFIX)]
    movers_files = sorted(glob.glob(os.path.join(
        paths.AI_DAILY_DIR, f"ai_daily_*{MOVERS_SUFFIX}")))

    envelopes: List[Tuple[str, Dict[str, Any], bool, datetime.date]] = []
    for json_path in movers_files + general_files:
        is_movers_file = json_path.endswith(MOVERS_SUFFIX)
        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("    [ai_daily] unreadable %s: %s", json_path, e)
            continue
        if not isinstance(data, dict):
            continue
        file_date = (parse_date_str(data.get("target_date"))
                     or parse_date_str(data.get("plan_date"))
                     or _ai_daily_file_date(json_path))
        if file_date is None:
            logger.warning("    [ai_daily] skipped %s (no date)",
                           os.path.basename(json_path))
            continue
        envelopes.append((json_path, data, is_movers_file, file_date))
    return envelopes


def load_ai_daily() -> List[Dict[str, Any]]:
    """AI daily market summaries — answers, geo asks, movers + references.

    Four row kinds per envelope:

      * the general summary answer — one row titled by the asked question,
        dated by the artifact's target_date;
      * the daily geopolitics/economy asks (embedded ``geo``) — one row
        per ask (global-English / China-Chinese news + market
        implications), dated by the artifact's target_date like the
        general summary;
      * the industry-movers answers — one row per ``asked`` survivor,
        titled by its question, dated by the movers plan_date and pre-set
        with the mover's own industry_id (keywords.extract_for_articles
        keeps a pre-set industry_id over its text match);
      * every search reference hit of the general summary, of each geo
        ask AND of each movers ask (:func:`hit_news_row`).
    """
    rows: List[Dict[str, Any]] = []
    n_ans = n_geo = n_movers = n_refs = 0
    for json_path, data, is_movers_file, file_date in iter_ai_daily_envelopes():
        if not is_movers_file:
            question = (data.get("question") or "").strip()
            answer = (data.get("answer") or "").strip()
            if question and answer:
                rows.append({
                    "title": question,
                    "content": answer,
                    "date": file_date,
                    "source": SOURCE,
                    "url": None,
                    "author": None,
                })
                n_ans += 1
            for hit in data.get("references") or []:
                if not isinstance(hit, dict):
                    continue
                row = hit_news_row(hit, file_date)
                if row is not None:
                    rows.append(row)
                    n_refs += 1

            # Daily geopolitics/economy asks (embedded by
            # downloads.macro.ai_daily under ``geo`` since the geo phase —
            # older artifacts carry none).
            geo_env = data.get("geo")
            if isinstance(geo_env, dict):
                for asked in geo_env.get("asks") or []:
                    if not isinstance(asked, dict):
                        continue
                    question = (asked.get("question") or "").strip()
                    answer = (asked.get("answer") or "").strip()
                    if not question or not answer:
                        continue
                    rows.append({
                        "title": question,
                        "content": answer,
                        "date": file_date,
                        "source": SOURCE,
                        "url": None,
                        "author": None,
                    })
                    n_geo += 1
                    for hit in asked.get("references") or []:
                        if not isinstance(hit, dict):
                            continue
                        row = hit_news_row(hit, file_date)
                        if row is not None:
                            rows.append(row)
                            n_refs += 1

        # Movers answers: the standalone file IS the envelope; the general
        # artifact embeds the same envelope under ``movers``.
        env = data if is_movers_file else data.get("movers")
        if not isinstance(env, dict):
            continue
        plan_date = parse_date_str(env.get("plan_date")) or file_date
        for asked in env.get("asked") or []:
            if not isinstance(asked, dict):
                continue
            question = (asked.get("question") or "").strip()
            answer = (asked.get("answer") or "").strip()
            if not question or not answer:
                continue
            rows.append({
                "title": question,
                "content": answer,
                "date": plan_date,
                "source": SOURCE,
                "url": None,
                "author": None,
                "industry_id":
                    (asked.get("industry_id") or "").strip() or None,
            })
            n_movers += 1
            # The movers ask's own search hits (embedded by
            # downloads.macro.ai_daily.movers since the --store removal —
            # older artifacts carry none).
            for hit in asked.get("references") or []:
                if not isinstance(hit, dict):
                    continue
                row = hit_news_row(hit, plan_date)
                if row is not None:
                    rows.append(row)
                    n_refs += 1

    logger.info("    [ai_daily] %d answers, %d geo, %d movers, %d refs",
                n_ans, n_geo, n_movers, n_refs)
    return rows
