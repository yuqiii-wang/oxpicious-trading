"""downloads.macro.ai_daily.topics — AI-mentioned topics -> Zhihu news.

The phase that closes the loop between the AI daily asks and the news
corpus: after the general summary / geo asks / movers asks produced the
day's answers, ONE plain chat completion (``llm_agents.llm_ask`` — no
web search attached) extracts the day's key topics from those answers,
and every topic becomes a Zhihu question-mode content search via the
existing :meth:`ZhihuNewsDownload.search_zhihu_question`.

Each topic search stores the standard
``search_<question>_<md5>_<today>.json`` artifact under
``temps/zhihu_news/`` — the same artifact shape the News page's question
bar produces — so ``builds.text`` ingests the topic hits into text.news
(+ text.news_comments via the per-item comments fetch) with zero loader
changes. The daily-tier ordering makes the composition safe: ai_daily
runs BEFORE the scheduled zhihu download; builds.text loads both
afterwards.

Topics are capped per run (``--max-topics``), deduped order-preserving —
the extractor lists the most important first. A failed extraction or a
failed per-topic search never kills the run: the outcome lands in the
phase envelope (``failed``) like the geo asks. The envelope embeds in
the general artifact under ``topic_searches``. The phase runs only when
ai_daily actually runs — the cached-artifact early return skips it like
every other phase (topic searches re-run with the ``--force`` re-ask and
overwrite the same-day artifact files, so a repeated run is idempotent).

The engine is the :class:`TopicSearches` class; the pure reply parser
and the constants stay module-level.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from llm_agents.llm_ask import AskOptions, get_provider as get_ask_provider
from llm_agents.online_search_summary import SearchSummary

from downloads.macro.zhihu.news import ZhihuNewsDownload, get_api_key

logger = logging.getLogger("ai_daily")

# Topic searches per run AT MOST (the extractor is asked for ~10 and the
# caller caps again via --max-topics).
MAX_TOPICS = 6
MIN_TOPIC_CHARS = 2   # drop 1-char noise tokens ("涨", "A股")
# The Zhihu question-mode query template — the platform's natural Q&A
# phrasing ("如何看待…？" is how the daily keyword asks too).
TOPIC_QUERY_TEMPLATE = "如何看待{topic}？"

# The extractor ask: system demands a bare JSON array (no prose, no code
# fence), the user question + the answers-as-context carries the task.
EXTRACT_SYSTEM_PROMPT = (
    "你是一个严谨的财经话题提取器。只输出一个 JSON 字符串数组，不要输出任何"
    "其他文字、解释或代码块标记。数组元素是简短的中文话题短语（每项不超过 12 个"
    "字，如“美联储降息”“半导体板块”），按重要性从高到低排列。"
)
EXTRACT_QUESTION = (
    "从下面的当天 AI 市场问答内容中，提取最值得进一步跟踪的财经话题"
    "（行业、事件、政策、公司等；去掉泛泛的大盘涨跌描述），"
    "按重要性从高到低最多列出 10 个。"
)


def parse_topics(answer: str) -> List[str]:
    """Parse the extractor's reply into a clean topic list.

    Robust to the usual LLM reply shapes: strips code fences, takes the
    first ``[...]`` block, ``json.loads`` it, then keeps only deduped
    non-trivial string topics (order-preserving — the extractor ranks by
    importance). An unparseable reply yields ``[]`` — the caller records
    the phase outcome and never raises.
    """
    if not answer:
        return []
    text = re.sub(r"```[a-zA-Z]*", "", answer)
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        raw = json.loads(match.group(0))
    except ValueError:
        return []
    if not isinstance(raw, list):
        return []
    topics: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        topic = item.strip()
        if len(topic) < MIN_TOPIC_CHARS or topic in topics:
            continue
        topics.append(topic)
    return topics


class TopicSearches:
    """The topic-searches phase engine: extract + per-topic Zhihu searches.

    Stateless — the AI answers arrive as the phase outputs the caller has
    already assembled (the general :class:`SearchSummary`, the geo asks
    envelope, the movers envelope). Mirrors the geo-ask failure contract:
    one failed extraction / search is recorded, never fatal.
    """

    def collect_answers(
        self,
        summary: Optional[SearchSummary],
        geo_env: Optional[Dict[str, Any]],
        movers_env: Optional[Dict[str, Any]],
    ) -> str:
        """Concatenate the day's AI answers into one extraction context.

        Whatever phases ran contribute: the general summary's answer, each
        geo ask's answer (labeled by scope), and each OK movers ask's
        question+answer (cached / weekly-skip / stale movers envelopes
        carry no asks). Empty string when nothing ran.
        """
        blocks: List[str] = []
        if summary is not None and summary.answer:
            blocks.append(f"【市场总结】{summary.answer}")
        if geo_env:
            for ask in geo_env.get("asks", []):
                answer = ask.get("answer")
                if answer:
                    blocks.append(f"【{ask.get('scope', 'geo')}新闻】{answer}")
        if movers_env and movers_env.get("status") == "ok":
            for outcome in movers_env.get("asked", []):
                answer = outcome.get("answer")
                if answer:
                    blocks.append(
                        f"【行业动态】{outcome.get('question', '')}\n{answer}")
        return "\n\n".join(blocks)

    async def extract_topics(
        self,
        answers: str,
        *,
        provider_name: str = "zhipu",
        model: Optional[str] = None,
    ) -> List[str]:
        """One plain chat completion -> the day's topic phrases.

        Through the ``llm_agents.llm_ask`` provider registry (NO web
        search attached — extraction only reads the given text); the
        answers ride in as the ask's *context*. Raises on transport
        failure — ``run`` catches and records it.
        """
        provider = get_ask_provider(provider_name)()
        opts = AskOptions(model=model, system=EXTRACT_SYSTEM_PROMPT,
                          temperature=0.2)
        result = await provider.ask(EXTRACT_QUESTION, opts, context=answers)
        topics = parse_topics(result.answer)
        logger.info("topics: extracted %d from %d chars of answers: %s",
                    len(topics), len(answers), topics)
        return topics

    def build_query(self, topic: str) -> str:
        """The Zhihu question-mode query for one topic."""
        return TOPIC_QUERY_TEMPLATE.format(topic=topic)

    def search_topic(
        self,
        topic: str,
        *,
        api_key: str,
        out_dir: Optional[Path] = None,
        with_comments: bool = True,
    ) -> Dict[str, Any]:
        """One Zhihu question-mode search for *topic*; return its envelope.

        Reuses :meth:`ZhihuNewsDownload.search_zhihu_question` verbatim —
        same request pacing (the service's ``search`` row), same artifact
        storage (``search_如何看待{topic}？_<md5>_<today>.json`` under
        ``temps/zhihu_news/``), same marker echo. *out_dir* overrides the
        artifact dir (probes); production runs leave it None.
        """
        question = self.build_query(topic)
        return ZhihuNewsDownload().search_zhihu_question(
            question, api_key, out_dir=out_dir, with_comments=with_comments)

    async def run(
        self,
        summary: Optional[SearchSummary],
        geo_env: Optional[Dict[str, Any]],
        movers_env: Optional[Dict[str, Any]],
        *,
        provider_name: str = "zhipu",
        model: Optional[str] = None,
        out_dir: Optional[Path] = None,
        max_topics: int = MAX_TOPICS,
        with_comments: bool = True,
    ) -> Dict[str, Any]:
        """The whole phase: collect answers -> extract topics -> search.

        Returns the phase envelope: ``{"status", "topics", "searched",
        "failed"}`` (plus ``error`` on an extraction failure) — ``status``
        is ``no-answers`` / ``extract-failed`` / ``no-topics`` / ``ok`` /
        ``partial`` (some topic searches failed). Per-topic failures never
        kill the batch.
        """
        answers = self.collect_answers(summary, geo_env, movers_env)
        if not answers.strip():
            logger.warning("topics: no AI answers collected — skipping")
            return {"status": "no-answers", "topics": [], "searched": [],
                    "failed": []}
        try:
            topics = await self.extract_topics(
                answers, provider_name=provider_name, model=model)
        except Exception as e:  # the phase must not kill the run
            logger.warning("topics: extraction FAILED %s: %s",
                           type(e).__name__, e)
            return {"status": "extract-failed",
                    "error": f"{type(e).__name__}: {e}",
                    "topics": [], "searched": [], "failed": []}
        if not topics:
            return {"status": "no-topics", "topics": [], "searched": [],
                    "failed": []}
        topics = topics[:max_topics]

        api_key = get_api_key()
        searched: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        for topic in topics:
            try:
                env = self.search_topic(
                    topic, api_key=api_key, out_dir=out_dir,
                    with_comments=with_comments)
                searched.append({
                    "topic": topic,
                    "question": env.get("question"),
                    "success": bool(env.get("success")),
                    "returned": env.get("returned", 0),
                    "comments": env.get("comments", 0),
                    "out_file": env.get("out_file"),
                })
                logger.info("topics: [%s] -> %s hit(s) (%s)", topic,
                            env.get("returned", 0), env.get("out_file"))
            except Exception as e:  # one failed search never kills the batch
                failed.append({"topic": topic,
                               "error": f"{type(e).__name__}: {e}"})
                logger.warning("topics: [%s] FAILED %s: %s", topic,
                               type(e).__name__, e)
        return {"status": "ok" if not failed else "partial",
                "topics": topics, "searched": searched, "failed": failed}
