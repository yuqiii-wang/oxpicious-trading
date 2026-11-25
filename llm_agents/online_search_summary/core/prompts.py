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
from typing import Optional, Sequence, Tuple

from llm_agents._core.prompts import LANG_LABELS, lang_label
from llm_agents.online_search_summary.core.models import SHANGHAI_TZ

# lang arg -> human label used inside prompts; unknown codes pass through
# (re-export of the llm_agents._core vocabulary).


SUMMARY_FORMAT_RULES = (
    "输出要求：\n"
    "1. 按重要性分点作答，每个要点必须同时包含：小标题、要点正文、引用来源标注、关键词。\n"
    "2. 小标题需概括该要点内容：中文小标题不少于8个汉字，英文小标题不少于5个单词。\n"
    "3. 每个要点正文必须至少引用一个来源，用[来源：ref_N]标注并注明来源日期；"
    "没有来源支持的要点不得输出。\n"
    "4. 每个要点末尾单独一行给出关键词，形如“关键词：词1、词2、词3”（3-8个）。\n"
    "5. 若问题指定了时间范围（如“截至2025年9月12日”），必须只引用发布时间与该时间"
    "范围相符的来源，检索时也必须带上问题中的年份和月份，"
    "不要引用发布时间晚于该日期的报道。\n"
    "6. 回答“当前/最新/近期/现状”类问题时，必须以今天日期为锚，只引用发布时间"
    "距今天不超过最近三个月的来源作为现状、最新数据与近期观点的依据；发布时间"
    "更早的来源只能支撑长期不变的背景事实（定义、产业链位置、历史沿革等），"
    "不得据此描述当前状态或最新变化，更不得把旧数据当作最新数据引用。")


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


def stale_refs_check_block(
    stale_refs: "Sequence[Tuple[str, Sequence[int]]]",
    today: datetime.date,
    anchor: Optional[datetime.date] = None,
) -> str:
    """The per-ref staleness DOUBLE-CHECK block appended to a compose user
    message when the whole-text date scan
    (:mod:`~llm_agents.online_search_summary.core.date_scan`) found
    references whose ENTIRE text only talks about years older than the
    anchor year — "scan only found 2023, but no {today.year}".

    *stale_refs* pairs each flagged reference's citation tag with the
    years its text mentions (newest-first sample rendered inline). Empty
    input -> "" (nothing appended; undated or anchor-year-carrying refs
    are never flagged).

    *anchor* is the date the QUESTION is about (the compose flow's
    ``anchor_date``) — None means the question is about the present, and
    the block keeps the today-anchored wording. With an anchor the same
    caution reads against the question's own time point: a reference
    whose text only mentions years older than the anchor year likely
    predates what the question asks about ("only 2022, nothing from the
    question's {anchor.year}") — *today* still renders truthfully as the
    run date.
    """
    if not stale_refs:
        return ""
    anchor = anchor or today
    if anchor == today:
        lines = [f"[来源时效性双重核查] 今天是{today:%Y-%m-%d}。对上述来源的全文"
                 "日期扫描发现，以下来源的全文只出现早于"
                 f"{today.year}年的年份，没有任何{today.year}年或之后的日期："]
    else:
        lines = [f"[来源时效性双重核查] 今天是{today:%Y-%m-%d}，问题所问的"
                 f"日期是{anchor:%Y-%m-%d}。对上述来源的全文日期扫描发现，"
                 "以下来源的全文只出现早于"
                 f"{anchor.year}年的年份，没有任何{anchor.year}年或之后的日期："]
    for tag, years in stale_refs:
        sample = "、".join(f"{y}年" for y in years) or "（未见任何年份）"
        lines.append(f"- {tag}（文中出现的年份：{sample}）")
    if anchor == today:
        lines.append(
            "这些来源很可能描述的是旧时点的情况，而非当前最新状态。请逐条双重核查："
            "除非某来源的内容明确属于长期不变的背景事实（定义、产业链位置、历史沿革"
            "等），不得用它支撑当前状态、最新数据或近期观点的要点；若确需引用，请在"
            "要点中注明其数据所属时点，不得当作最新情况表述。")
    else:
        lines.append(
            "这些来源很可能描述的是问题时点之前或之后的旧情况，而非所问时点的状态。"
            "请逐条双重核查：除非某来源的内容明确属于长期不变的背景事实（定义、"
            "产业链位置、历史沿革等），不得用它支撑所问时点的状态、数据或观点的"
            "要点；若确需引用，请在要点中注明其数据所属时点，不得当作所问时点的"
            "情况表述。")
    return "\n".join(lines)
