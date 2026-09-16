"""llm_agents.news_digestions.agent — Per-article LLM digestion.

Digests one text.news article into a summary + a sentiment_level score in
[-5, 5] (the text.news_digestions contract, DDL:
database/sql/text/04_news_digestions.sql):

  * ``digest_system_prompt`` / ``build_digest_messages`` — the Chinese
    analyst prompt: 2-4 sentence summary of event / actors / market
    implication, plus a -5 (extremely bearish) … +5 (extremely bullish)
    score; strict-JSON output contract ``{"summary": …, "sentiment": …}``.
  * ``parse_digest_json``   — fence-tolerant strict-JSON parsing; the
    sentiment is clamped to [-5, 5] so a runaway model can never violate
    the ck_news_digestions_sentiment CHECK.
  * ``digest_article``      — one article -> one digestion row dict; on an
    unparseable reply it retries ONCE with a stricter instruction (the
    provider's own transport/429/5xx retrying lives in
    BaseOnlineSearchProvider._post_json, not here).

The LLM call itself is delegated to a registered online-search provider's
``_chat_complete`` (plain chat completion — no web search involved; zhihu
delegates to its chat provider). Digestions are content-only, so the model
never sees anything beyond the stored article.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from llm_agents.online_search_summary import (
    BaseOnlineSearchProvider, OnlineSearchError, lang_label,
)

logger = logging.getLogger(__name__)


class DigestionError(RuntimeError):
    """The model reply could not be parsed into a digestion."""


def digest_system_prompt(lang: str = "zh") -> str:
    """System prompt: summary + [-5, 5] sentiment as strict JSON."""
    return ("你是一位严谨的证券研究分析师。请阅读给定的新闻文章，写一段简明摘要，"
            "并就该文章对相关行业/市场的预期影响给出情绪评分。\n"
            "输出要求：\n"
            "1. 摘要 2-4 句，覆盖事件、涉及主体、以及对行业/市场的含义；"
            "只使用文章中的信息，不要编造。\n"
            "2. sentiment 为 -5 到 5 之间的数字（可含一位小数）："
            "-5 极度利空，0 中性，+5 极度利好。\n"
            "3. 严格只输出一个 JSON 对象，格式："
            '{"summary": "…", "sentiment": <数字>}，不要输出 JSON 以外的任何文字。\n'
            f"全文必须使用{lang_label(lang)}作答。")


def build_digest_messages(article: Dict[str, Any], *, content: str,
                          lang: str = "zh") -> List[Dict[str, str]]:
    """System + user messages embedding the stored article fields."""
    date = article["date"]
    user_msg = (
        f"标题：{article['title']}\n"
        f"来源：{article.get('source') or '-'}\n"
        f"日期：{date.isoformat() if hasattr(date, 'isoformat') else date}\n"
        f"行业：{article.get('industry_id') or '未知'}\n\n"
        f"正文：\n{content or '（无正文，仅标题）'}")
    return [{"role": "system", "content": digest_system_prompt(lang)},
            {"role": "user", "content": user_msg}]


def parse_digest_json(text: str) -> "tuple[str, Optional[float]]":
    """Parse a model reply into (summary, sentiment); sentiment or None.

    Tolerates ``` fences and prose around the JSON object. sentiment is
    clamped to [-5, 5] (the DDL CHECK would reject anything wider) and
    rounded to 2 decimals. A missing/garbage sentiment raises — the caller
    decides whether the whole digestion fails; an ABSENT sentiment key is
    also an error, since the score is the point of the digestion.
    """
    if not text or not text.strip():
        raise DigestionError("empty completion")
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise DigestionError(f"no JSON object in reply: {text[:120]!r}")
    try:
        data = json.loads(s[start:end + 1])
    except json.JSONDecodeError as e:
        raise DigestionError(
            f"invalid JSON ({e}): {text[:120]!r}") from None
    if not isinstance(data, dict):
        raise DigestionError(f"JSON is not an object: {text[:120]!r}")
    summary = str(data.get("summary") or "").strip()
    if not summary:
        raise DigestionError("reply carries no summary")
    raw = data.get("sentiment", data.get("sentiment_level"))
    if raw is None:
        raise DigestionError("reply carries no sentiment score")
    try:
        level = round(float(raw), 2)
    except (TypeError, ValueError):
        raise DigestionError(f"non-numeric sentiment {raw!r}") from None
    return summary, max(-5.0, min(5.0, level))


def truncate_content(content: Optional[str], max_chars: int) -> str:
    """Trim the article body to *max_chars* so prompts stay bounded."""
    content = (content or "").strip()
    if max_chars and len(content) > max_chars:
        return content[:max_chars] + " …[截断]"
    return content


async def digest_article(
    provider: BaseOnlineSearchProvider,
    article: Dict[str, Any],
    *,
    model: str,
    lang: str = "zh",
    max_content_chars: int = 8000,
) -> Dict[str, Any]:
    """Digest one fetched text.news row -> one text.news_digestions row.

    Returns the store-shaped dict (news_id, date, industry_id, summary,
    sentiment_level, llm_model). Raises DigestionError / OnlineSearchError
    after ONE parse retry; transport/API retries are the provider's.
    """
    messages = build_digest_messages(
        article, content=truncate_content(article.get("content"),
                                          max_content_chars),
        lang=lang)
    last_err: Optional[Exception] = None
    for attempt in (1, 2):
        try:
            reply, _usage = await provider._chat_complete(messages, model=model)
            summary, sentiment = parse_digest_json(reply)
            return {
                "news_id": article["news_id"],
                "date": article["date"],
                "industry_id": article.get("industry_id"),
                "summary": summary,
                "sentiment_level": sentiment,
                "llm_model": model,
            }
        except (OnlineSearchError, DigestionError) as e:
            last_err = e
            if attempt == 1 and isinstance(e, DigestionError):
                # Parse failures are worth one stricter retry; API errors
                # have already been retried by the provider transport.
                logger.warning("    [digest] news_id=%s parse failed (%s) — "
                               "retrying once", article["news_id"], e)
                messages = messages + [{
                    "role": "user",
                    "content": "上一次输出无法解析。请严格只输出一个 JSON 对象："
                               '{"summary": "…", "sentiment": <数字>}，'
                               "不要输出任何其他文字。"}]
                continue
            break
    raise last_err  # type: ignore[misc]
