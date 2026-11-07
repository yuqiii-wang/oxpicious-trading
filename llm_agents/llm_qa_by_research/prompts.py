"""llm_agents.llm_qa_by_research.prompts — the six-request prompt contract.

The generation flow (``generation.py``) issues SIX separate LLM requests
per research Q&A row:

  * requests 1-5 — one per evaluation dimension (:class:`DimensionSpec`
    registry ``DIMENSIONS``): each is a dedicated web-searched question
    (``dimension_search_query`` for retrieval, ``dimension_question`` for
    the answer) whose reply lands in its own llm.llm_qa_by_research
    column — intro (general investors' logic), optimistic_forecast,
    pessimistic_forecast, risks, capitalization (current market-capital
    interests);
  * request 6 — ``summary_system_prompt``: a strict-JSON request that
    SUMMARIZES the five dimension answers into the row's ``answer`` and
    assigns the ``logic_label`` (parsed by :func:`parse_summary_json`,
    the news_digestions strict-JSON pattern).

All prompts are Chinese-first (``lang`` param follows the
``lang_label`` vocabulary); dimension answers keep inline
``[来源：ref_N]`` citations aligned with the hits handed to
``summarize_hits_via_compose``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from _common.labels import RESEARCH_LOGIC_CODE
from llm_agents._core.prompts import lang_label

from llm_agents.llm_qa_by_research.data_definitions import (
    DimensionSpec,
)

class SummaryParseError(RuntimeError):
    """The 5th request's reply could not be parsed into summary + label."""




# The five evaluation dimensions, in UI/storage order. {subject} in *ask*
# is the research question verbatim.




DIMENSIONS: Tuple[DimensionSpec, ...] = (
    DimensionSpec(
        key="intro", column="intro", title="投资逻辑总览",
        ask=("面向普通投资者，介绍{subject}的基本面与投资逻辑：它是什么、"
             "做什么、处于产业链的什么位置，理解它的一般框架是什么。"),
        search_suffix="基本面 介绍 投资逻辑 产业链"),
    DimensionSpec(
        key="optimistic_forecast", column="optimistic_forecast",
        title="乐观前景预测",
        ask=("展望未来6-12个月，{subject}在乐观情景下的前景如何？"
             "利好条件（政策支持、需求超预期、技术突破、流动性宽松等）"
             "兑现时的发展路径、回报弹性与主要催化剂，"
             "并说明乐观情景成立的条件与观察信号。"),
        search_suffix="前景预测 乐观 展望 催化剂 目标价"),
    DimensionSpec(
        key="pessimistic_forecast", column="pessimistic_forecast",
        title="悲观前景预测",
        ask=("展望未来6-12个月，{subject}在悲观情景下的前景如何？"
             "利空条件（需求回落、政策收紧、竞争恶化、流动性收紧等）"
             "兑现时的下行路径、主要风险暴露与可能的回撤来源，"
             "并说明悲观情景成立的条件与预警信号。"),
        search_suffix="前景预测 悲观 下行 风险 看空"),
    DimensionSpec(
        key="risks", column="risks", title="风险因素",
        ask=("{subject}当前最主要的投资风险有哪些？覆盖政策监管、需求回落、"
             "竞争加剧与商品化、技术替代、杠杆与流动性等，"
             "说明各风险的触发条件与可能影响。"),
        search_suffix="投资风险 政策 监管 竞争 需求"),
    DimensionSpec(
        key="capitalization", column="capitalization", title="市值与市场关注",
        ask=("{subject}当前的市值结构与市场资金关注状况如何？包括市值规模"
             "与板块分布、相关指数中的权重、机构与散户的关注度、"
             "近期资金流向与持仓变化。"),
        search_suffix="市值 指数权重 机构持仓 资金流向"),
)

DIMENSIONS_BY_KEY: Dict[str, DimensionSpec] = {d.key: d for d in DIMENSIONS}


def dimension_question(spec: DimensionSpec, subject: str) -> str:
    """The dimension's ASK question ({subject} substituted)."""
    return spec.ask.format(subject=subject)


def dimension_search_query(spec: DimensionSpec, subject: str) -> str:
    """The dimension's retrieval query (subject + dimension terms)."""
    return f"{subject} {spec.search_suffix}"


def dimension_system_prompt(spec: DimensionSpec, lang: str = "zh") -> str:
    """System prompt for one dimension request (refs-cited answer).

    Used by BOTH request variants: the searched one (hits handed to
    ``summarize_hits_via_compose``, which embeds them and the ask question
    in its own user message) and the no-hits plain fallback (the provider's
    ``_chat_complete`` over :func:`dimension_plain_user_message`)."""
    return (
        "你是一位严谨的证券研究分析师，正在为投资者整理某一行业/标的的"
        "底层研究资料。请只围绕给定维度的要求作答：\n"
        f"1. 输出 3-6 个要点，每个要点以简短小标题开头（8 字以上），"
        "再展开 1-3 句说明；\n"
        "2. 要点中的事实请标注对应的搜索来源，格式为 [来源：ref_N]，"
        "与编号一致；搜索结果未覆盖的内容不要编造，可以基于常识给出"
        "分析但要标明是分析；\n"
        f"3. 全文必须使用{lang_label(lang)}作答，不要输出与该维度无关的内容。")


def dimension_plain_user_message(
    spec: DimensionSpec, subject: str,
) -> str:
    """User message for the NO-HITS plain fallback of a dimension request
    (search surfaced nothing; the model answers from its own knowledge)."""
    import datetime

    from llm_agents._core.models import SHANGHAI_TZ

    return (
        f"今天的日期是"
        f"{datetime.datetime.now(SHANGHAI_TZ):%Y-%m-%d}。\n\n"
        f"研究主题：{subject}\n研究维度：{spec.title}\n"
        f"（本次没有搜索结果，请基于你掌握的知识作答，并明确哪些是分析。）\n\n"
        f"问题：{dimension_question(spec, subject)}")


# ----------------------------------------------------------------------------
# Request 5 — summary + logic_label (strict JSON)
# ----------------------------------------------------------------------------
# zh aliases the model may answer the label with -> canonical label name.
_LABEL_ALIASES: Dict[str, str] = {
    "growth": "growth", "成长": "growth", "成长逻辑": "growth",
    "value": "value", "价值": "value", "价值逻辑": "value",
    "dividend": "dividend", "红利": "dividend", "股息": "dividend",
    "commodity": "commodity", "商品": "commodity", "大宗商品": "commodity",
    "cyclical": "cyclical", "周期": "cyclical", "周期逻辑": "cyclical",
    "defensive": "defensive", "防御": "defensive", "防御性": "defensive",
    "quality": "quality", "质量": "quality", "质量逻辑": "quality",
    "theme": "theme", "主题": "theme", "题材": "theme",
    "other": "other", "其他": "other", "其它": "other",
}


def summary_system_prompt(lang: str = "zh") -> str:
    """System prompt for the 5th request: summarize + label, strict JSON."""
    glosses = (
        "growth=成长（盈利/收入高增长驱动）、value=价值（低估值/均值回归）、"
        "dividend=红利（高分红/股息回报）、commodity=商品（大宗商品价格驱动）、"
        "cyclical=周期（经济与量价周期驱动）、defensive=防御（低波动/避险需求）、"
        "quality=质量（高ROE/护城河/确定性溢价）、theme=主题（题材/政策催化）、"
        "other=其他（以上均不符合）")
    return ("你是一位严谨的证券研究分析师。给定五个研究维度的要点整理"
            "（投资逻辑总览/乐观前景预测/悲观前景预测/风险因素/市值与市场关注），"
            "请：\n"
            "1. 写一段 3-5 句的总结，概括该行业/标的最主要的投资逻辑、"
            "多空情景下的前景与核心风险，只依据给定材料，不要编造；\n"
            "2. 从下列逻辑标签中选出最主导的一个，作为 logic_label：\n"
            f"{glosses}。\n"
            "3. 严格只输出一个 JSON 对象，格式："
            '{"summary": "…", "logic_label": "…"}，'
            "不要输出 JSON 以外的任何文字。\n"
            f"summary 必须使用{lang_label(lang)}撰写。")


def build_summary_user_message(
    subject: str,
    dimension_texts: List[Tuple[DimensionSpec, str]],
) -> str:
    """User message for the 5th request: the four dimensions embedded."""
    blocks = []
    for spec, text in dimension_texts:
        blocks.append(f"【{spec.title}】\n{text.strip()}")
    return (f"研究主题：{subject}\n\n"
            + "\n\n".join(blocks)
            + "\n\n请按系统指令输出 JSON（summary + logic_label）。")


def normalize_label(raw: str) -> str:
    """Canonical research_logic label name for a model-returned label
    (accepts the English names and common zh aliases); '' when unknown."""
    key = raw.strip().lower()
    return _LABEL_ALIASES.get(key, _LABEL_ALIASES.get(raw.strip(), ""))


def parse_summary_json(text: str) -> Tuple[str, str]:
    """Parse the 5th request's reply into (summary, label_name).

    Tolerates ``` fences and prose around the JSON object (the
    news_digestions strict-JSON pattern). The label accepts the English
    vocabulary names and zh aliases; an unknown/missing label or an empty
    summary raises :class:`SummaryParseError` (the caller retries once).
    """
    if not text or not text.strip():
        raise SummaryParseError("empty completion")
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise SummaryParseError(f"no JSON object in reply: {text[:120]!r}")
    try:
        data = json.loads(s[start:end + 1])
    except json.JSONDecodeError as e:
        raise SummaryParseError(
            f"invalid JSON ({e}): {text[:120]!r}") from None
    if not isinstance(data, dict):
        raise SummaryParseError(f"JSON is not an object: {text[:120]!r}")
    summary = str(data.get("summary") or "").strip()
    if not summary:
        raise SummaryParseError("reply carries no summary")
    label = normalize_label(str(data.get("logic_label") or ""))
    if not label:
        raise SummaryParseError(
            f"unknown logic_label {data.get('logic_label')!r} "
            f"(expected one of {sorted(RESEARCH_LOGIC_CODE)})")
    return summary, label
