"""llm_agents.llm_ask.core.prompts — Ask prompt builders.

Two prompt flows live here:

  * the default plain-ask system prompt (``ask_system_prompt``) — the
    mirror of ``llm_agents.online_search_summary.core.prompts`` for the
    NO-retrieval flow: the model answers from its own knowledge only,
    flags uncertain or time-sensitive facts instead of fabricating, and
    replies in the requested language;
  * the chart-adviser flow (``adviser_system_prompt`` +
    ``chart_context_block``) behind the data_viz "AI Ask" feature: the
    system prompt sets the quantitative chart-adviser persona, the
    context block renders one AiAskPlotInfo payload into the readable
    prefix of the user message (screenshots themselves travel as image
    parts, not text).

The language-label vocabulary is the shared ``llm_agents._core.prompts``
one, re-exported here. Pure string building — no network, no DB.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from llm_agents._core.prompts import LANG_LABELS, lang_label


def ask_system_prompt(lang: str = "zh") -> str:
    """Default system prompt for the plain LLM ask flow."""
    return ("你是一位严谨的研究分析师。请基于你已掌握的知识回答用户问题，"
            "你没有联网检索能力，不要编造无法确认的事实。\n"
            "输出要求：\n"
            "1. 按重要性分点作答，每个要点以简明的小标题开头。\n"
            "2. 涉及不确定或可能过时的信息（如最新行情、近期事件），必须明确说明"
            "其不确定性或知识时效，不得虚构具体数字。\n"
            f"全文必须使用{lang_label(lang)}作答。")


def adviser_system_prompt() -> str:
    """System prompt for the chart-adviser flow (the data_viz AI Ask).

    The user message carries one chart's plot info (instruments, window,
    per-series stats) and, when the model accepts images, the chart's
    canvas screenshots. Answers mirror the question's language — the UI
    is bilingual, so no fixed lang is forced.
    """
    return ("你是一位严谨的量化金融图表分析顾问。用户会给出一张金融图表的上下文"
            "（图表说明、标的、时间窗口、各系列的语义与统计摘要），"
            "并可能附带图表截图。\n"
            "回答要求：\n"
            "1. 先读懂图表上下文与截图再作答；引用数字时以上下文给出的统计为准，"
            "截图只作视觉参考，不要从截图中读数。\n"
            "2. 按重要性分点作答，每个要点以简明的小标题开头。\n"
            "3. 明确区分事实描述与你的推断；对行情的看法给出情景与风险提示，"
            "并说明这不构成投资建议。\n"
            "4. 涉及不确定或超出所给窗口的信息，明确说明其不确定性，不得虚构。\n"
            "请使用与用户问题相同的语言作答。")


def adviser_search_system_prompt() -> str:
    """System prompt for the chart-adviser flow WITH online search (the
    data_viz AI Ask "online search" tick).

    Same chart-adviser persona as ``adviser_system_prompt``, but the user
    message also embeds web-search references and every extra-chart claim
    must carry an inline [来源：ref_N] citation. Text-only — screenshots
    never ride along on this path (the search summarize flow has no image
    input). Answers mirror the question's language.
    """
    return ("你是一位严谨的量化金融图表分析顾问。用户会给出一张金融图表的上下文"
            "（图表说明、标的、时间窗口、各系列的语义与统计摘要）以及一份网络搜索"
            "结果，请结合两者回答用户问题。\n"
            "回答要求：\n"
            "1. 图表本身的数字以上下文给出的统计为准；图表之外的信息（新闻、事件、"
            "宏观背景等）以搜索结果为准，引用时用[来源：ref_N]标注并注明来源日期。\n"
            "2. 按重要性分点作答，每个要点以简明的小标题开头。\n"
            "3. 明确区分事实描述与你的推断；对行情的看法给出情景与风险提示，"
            "并说明这不构成投资建议。\n"
            "4. 图表上下文与搜索结果都没有的信息不要编造。\n"
            "请使用与用户问题相同的语言作答。")


def chart_context_block(plot_info: Mapping[str, Any], theme_mode: Optional[str],
                        image_count: int) -> str:
    """Render one AiAskPlotInfo dict into the readable context prefix of
    the adviser user message (pure string building, defensive against
    shape drift — missing keys degrade to fewer lines, never an error).

    ``image_count`` is the number of screenshots actually attached (0
    when the model takes no images) — the block tells the model whether
    to expect any, and ``theme_mode`` explains the screenshot colors.
    ``plot_info["date"]`` (the modal's pinned as-of date, "YYYY-MM-DD" or
    year-month "YYYY-MM") renders as the 指定日期 line — the reference
    "now" for time-relative questions — when present.
    """
    lines: list[str] = []

    chart = plot_info.get("chart") or {}
    title = chart.get("title") or "chart"
    if chart.get("kind"):
        lines.append(f"图表：{title}（{chart['kind']}）")
    else:
        lines.append(f"图表：{title}")
    if chart.get("subtitle"):
        lines.append(f"副标题：{chart['subtitle']}")
    if chart.get("intro"):
        lines.append(f"图表说明：{chart['intro']}")

    scope = plot_info.get("scope") or {}
    instruments = scope.get("instruments") or []
    if instruments:
        parts = [f"{i.get('code', '?')}"
                 + (f" {i['name']}" if i.get("name") else "")
                 + (f"（{i['assetClass']}）" if i.get("assetClass") else "")
                 for i in instruments if isinstance(i, dict)]
        lines.append(f"标的：{'、'.join(parts)}")
    if scope.get("industry"):
        lines.append(f"行业：{scope['industry']}")
    if scope.get("sector"):
        lines.append(f"板块：{scope['sector']}")

    window = plot_info.get("window") or {}
    win = "、".join(
        f"{k}={window[k]}" for k in ("start", "end", "granularity")
        if window.get(k))
    if win:
        lines.append(f"时间窗口：{win}")

    # The modal's as-of date selector (exact "YYYY-MM-DD" or year-month
    # "YYYY-MM", seeded with the chart's latest plotted date) — the anchor
    # the answer's time-relative wording must resolve against.
    as_of = plot_info.get("date")
    if isinstance(as_of, str) and as_of.strip():
        as_of = as_of.strip()
        precision = "年月" if len(as_of) == 7 else "具体日期"
        lines.append(
            f"指定日期：{as_of}（{precision}）"
            "——回答涉及时点（如“当前/最新/最近”）时以该日期为参考时点。")

    state = plot_info.get("state") or {}
    if state:
        kv = "、".join(f"{k}={v}" for k, v in state.items())
        lines.append(f"当前视图状态：{kv}")

    for s in plot_info.get("series") or []:
        if not isinstance(s, dict):
            continue
        seg = f"- {s.get('name', '?')}（{s.get('kind', '?')}）"
        if s.get("unit"):
            seg += f" 单位={s['unit']}"
        if s.get("description"):
            seg += f"：{s['description']}"
        stats = s.get("stats") or {}
        nums = "、".join(
            f"{k}={stats[k]}" for k in ("count", "first", "last", "min", "max")
            if stats.get(k) is not None)
        if nums:
            seg += f"｜{nums}"
        lines.append(seg)

    for note in plot_info.get("notes") or []:
        if note:
            lines.append(f"说明：{note}")

    if image_count > 0:
        theme = "深色" if theme_mode == "dark" else "浅色"
        lines.append(f"随本消息附上 {image_count} 张图表截图（{theme}背景），"
                     "反映用户当前看到的视图。")
    else:
        lines.append("本次未附图表截图，请仅依据上述上下文作答。")

    return "\n".join(lines)
