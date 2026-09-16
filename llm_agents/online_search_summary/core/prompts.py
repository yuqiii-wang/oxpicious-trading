"""llm_agents.online_search_summary.core.prompts — Summary prompt builders.

The output contract shared by both summary flows (native search-in-chat
and the compose flow) plus its prompt builders:

  * ``SUMMARY_FORMAT_RULES`` — EVERY key point must carry a subtitle, an
    inline citation, and keywords;
  * ``summary_system_prompt`` — system message for the compose flow
    (plain chat completion over embedded search results);
  * ``native_search_prompt`` — tool ``search_prompt`` for the native
    (search-in-chat) flow; the literal ``{search_result}`` placeholder is
    substituted by ZhiPu server-side and MUST survive in the string.

Pure string building — no network, no DB.
"""
from __future__ import annotations

import datetime
from typing import Optional

from llm_agents.online_search_summary.core.models import SHANGHAI_TZ

# lang arg -> human label used inside prompts; unknown codes pass through.
LANG_LABELS = {"zh": "中文", "en": "English"}


def lang_label(lang: str) -> str:
    return LANG_LABELS.get((lang or "zh").lower(), lang or "中文")


SUMMARY_FORMAT_RULES = (
    "输出要求：\n"
    "1. 按重要性分点作答，每个要点必须同时包含：小标题、要点正文、引用来源标注、关键词。\n"
    "2. 小标题需概括该要点内容：中文小标题不少于8个汉字，英文小标题不少于5个单词。\n"
    "3. 每个要点正文必须至少引用一个来源，用[来源：ref_N]标注并注明来源日期；"
    "没有来源支持的要点不得输出。\n"
    "4. 每个要点末尾单独一行给出关键词，形如“关键词：词1、词2、词3”（3-8个）。\n"
    "5. 若问题指定了时间范围（如“截至2025年9月12日”），必须只引用发布时间与该时间"
    "范围相符的来源，检索时也必须带上问题中的年份和月份，"
    "不要引用发布时间晚于该日期的报道。")


def summary_system_prompt(lang: str = "zh") -> str:
    """System prompt for the compose flow (plain chat completion)."""
    return ("你是一位严谨的研究分析师。请仅依据给定的网络搜索结果回答用户问题，"
            "按重要性归纳关键信息。\n"
            f"{SUMMARY_FORMAT_RULES}\n"
            f"全文必须使用{lang_label(lang)}作答。搜索结果中没有的信息不要编造。")


def native_search_prompt(lang: str = "zh", today: Optional[str] = None) -> str:
    """Tool ``search_prompt`` for the native (search-in-chat) flow.

    The literal ``{search_result}`` placeholder is substituted by ZhiPu
    server-side and MUST survive in the returned string; ``today`` is
    filled here so the model has a recency anchor.
    """
    today = today or datetime.datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
    return ("你是一位严谨的研究分析师。请总结网络搜索{{search_result}}"
            "中的关键信息，按重要性排序。\n"
            f"{SUMMARY_FORMAT_RULES}\n"
            f"全文必须使用{lang_label(lang)}作答，只使用搜索结果中给出的信息，"
            f"没有的内容不要编造。今天的日期是{today}。")
