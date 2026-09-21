"""llm_agents.llm_ask.payload — AI Ask payload files (the data_viz link).

The data_viz "AI Ask" feature (the "?" beside chart titles) POSTs
``{question, plotInfo, screenshots, themeMode, onlineSearch, searchQuery}``
to the Express ai-ask service, which writes it to a JSON file under
``temp_scripts/ai_ask/`` and spawns this package as::

    python -m llm_agents.llm_ask ask --payload-file <rel-path> --json

(a multi-hundred-KB base64 screenshot cannot travel as an argv string —
Windows' 32k command-line limit). This module is the python half of that
contract: read the file, validate + normalize it, DELETE it (the Express
service only best-effort-unlinks as a backstop), and build the
chart-adviser ask request:

  * plain path — system: the chart-adviser persona prompt; context: the
    plot info rendered by ``chart_context_block``; images: the
    screenshots as data URLs, attached only when the model accepts image
    input; the model defaults to the provider's vision model
    (``core.vision.DEFAULT_VISION_MODELS``) when screenshots are present
    and no explicit model was given, so the default text model never
    silently loses the screenshots.
  * online-search path (``onlineSearch: true`` — the modal's "online
    search" tick) — ``ask_online_search`` routes through the
    ``online_search_summary`` agent: web-search the search query
    (``searchQuery`` — the modal's search line, seeded with chart title +
    date; the question when blank), then compose the adviser answer over
    the chart context + the retrieved references ([来源：ref_N] citations
    kept; a compact source list is appended for the modal). Text-only —
    screenshots are dropped.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from llm_agents.llm_ask.core.errors import LlmAskError
from llm_agents.llm_ask.core.models import AskResult, AskOptions
from llm_agents.llm_ask.core.prompts import (
    adviser_search_system_prompt, adviser_system_prompt,
    chart_context_block,
)
from llm_agents.llm_ask.providers.base import BaseLlmAskProvider
from llm_agents.llm_ask.core.vision import (
    default_vision_model, model_accepts_images,
)
# search vocabulary only (pure dataclasses); the provider/network stack is
# imported lazily inside ask_online_search so the plain path stays lean.
from llm_agents.online_search_summary.core.models import (
    SearchHit, SearchOptions, SearchSummary,
)

logger = logging.getLogger(__name__)

# Mirror of the Express service caps (ai-ask.service.ts): per-image
# base64 size and total screenshots per ask.
MAX_IMAGE_CHARS = 4 * 1024 * 1024
MAX_IMAGES = 4


@dataclass
class AiAskPayload:
    """One validated AI Ask request from the Express ai-ask service."""

    question: str
    plot_info: Dict[str, Any]
    screenshots: Tuple[str, ...] = ()
    theme_mode: Optional[str] = None
    online_search: bool = False
    # Web-search term (the modal's search line, seeded with chart title +
    # date) — searched instead of the question; None falls back to it.
    search_query: Optional[str] = None


def read_payload_file(path: str) -> AiAskPayload:
    """Read + validate one AI Ask payload file, then delete it.

    Raises ``ValueError`` on a malformed payload (the CLI turns that into
    a failed marker-free exit the service reports via its stderr tail).
    The file is deleted after a successful read — screenshots travel the
    file exactly once; afterwards they are persisted (if at all) by
    llm_agents.llm_ask.storage, so the payload file itself stays throwaway.
    """
    with open(path, "r", encoding="utf-8") as fh:
        body = json.load(fh)
    if not isinstance(body, dict):
        raise ValueError("payload file must contain a JSON object")
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("payload 'question' must be a non-empty string")
    plot_info = body.get("plotInfo")
    if not isinstance(plot_info, dict):
        raise ValueError("payload 'plotInfo' must be an object")
    raw_shots = body.get("screenshots")
    if raw_shots is None:
        raw_shots = []
    if not isinstance(raw_shots, list):
        raise ValueError("payload 'screenshots' must be an array")
    screenshots = tuple(
        s for s in raw_shots
        if isinstance(s, str) and s.startswith("data:image/")
        and len(s) <= MAX_IMAGE_CHARS
    )[:MAX_IMAGES]
    if len(screenshots) != len(raw_shots):
        logger.warning("payload: kept %d of %d screenshot(s) after "
                       "data-url/size/count filtering",
                       len(screenshots), len(raw_shots))
    theme_mode = body.get("themeMode")
    if theme_mode not in ("light", "dark"):
        theme_mode = None
    search_query = body.get("searchQuery")
    if not isinstance(search_query, str) or not search_query.strip():
        search_query = None
    try:
        os.unlink(path)
    except OSError:
        logger.warning("payload: could not delete %s", path, exc_info=True)
    return AiAskPayload(
        question=question.strip(),
        plot_info=plot_info,
        screenshots=screenshots,
        theme_mode=theme_mode,
        online_search=body.get("onlineSearch") is True,
        search_query=search_query.strip() if search_query else None,
    )


def build_ask_request(
    payload: AiAskPayload, provider_name: str,
    *, model: Optional[str] = None,
) -> Tuple[str, AskOptions, str]:
    """Turn one payload into the ``(question, AskOptions, context)``
    adviser ask.

    ``model`` (the CLI ``--model`` override) wins; otherwise the request
    carries screenshots only a vision model can honor, so the model
    defaults to the provider's vision model when screenshots are present
    and to the provider default otherwise.
    """
    resolved_model = model
    if resolved_model is None and payload.screenshots:
        resolved_model = default_vision_model(provider_name)
        if resolved_model:
            logger.info("payload: %d screenshot(s) -> vision model %s",
                        len(payload.screenshots), resolved_model)
    opts = AskOptions(
        model=resolved_model,
        system=adviser_system_prompt(),
        images=payload.screenshots,
    )
    # count only the images that will actually attach — a forced
    # text-only model drops them in BaseLlmAskProvider.ask, and the
    # context block must not promise a screenshot that never arrives.
    attached = (
        len(payload.screenshots)
        if payload.screenshots and resolved_model is not None
        and model_accepts_images(provider_name, resolved_model)
        else 0)
    context = chart_context_block(
        payload.plot_info, payload.theme_mode, attached)
    return payload.question, opts, context


def _format_source_list(summary: SearchSummary) -> str:
    """Compact reference appendix for the modal — the compose answer cites
    [来源：ref_N] inline, and the tick UI renders plain text, so the ref
    tags need their legend one screen away. Only the CITED refs are
    listed (a web search can return 50 hits; uncited ones are noise)."""
    cited = set(summary.cited_refs)
    lines: List[str] = []
    for hit in summary.hits:
        if cited and hit.refer not in cited:
            continue
        meta = " · ".join(str(x) for x in (
            hit.media, hit.publish_date, hit.link) if x)
        lines.append(
            f"[{hit.refer}] {hit.title}（{meta}）" if meta
            else f"[{hit.refer}] {hit.title}")
    return "\n".join(lines)


async def ask_online_search(payload: AiAskPayload,
                            ask_provider: BaseLlmAskProvider,
                            *, model: Optional[str] = None) -> AskResult:
    """AI Ask chart-adviser flow WITH online search (the modal's tick).

    Routes through the ``online_search_summary`` agent: web-search the
    search query (``searchQuery`` — the modal's search line, seeded with
    chart title + date; falls back to the QUESTION when blank — search
    engines want a topic, not the chart dump), then
    compose the adviser answer over the chart context + the retrieved
    references via ``summarize_hits_via_compose``. Text-only — the search
    summarize flow has no image input, so screenshots are dropped with a
    warning. The answer keeps its inline [来源：ref_N] citations; a
    compact source list is appended for the modal. When the search
    returns no references (e.g. an English question against the
    Chinese-focused engines), falls back to the plain adviser ask with a
    notice — the tick must never fail the modal.
    """
    # lazy imports: the plain-ask path must never pull the search stack in
    from llm_agents.online_search_summary.providers.registry import (
        PROVIDERS as SEARCH_PROVIDERS,
        get_provider as get_search_provider,
    )
    provider_name = ask_provider.name
    if provider_name not in SEARCH_PROVIDERS:
        raise LlmAskError(
            f"online search: provider {provider_name!r} has no search "
            f"integration (known: {sorted(SEARCH_PROVIDERS)})")

    search_term = payload.search_query or payload.question
    logger.info("online-search ask: searching %r (question %r)",
                search_term[:60], payload.question[:60])
    provider = get_search_provider(provider_name)()
    resp = await provider.search(search_term, SearchOptions())
    if not resp.hits:
        logger.warning(
            "online-search ask: no references for %r — falling back to "
            "the plain adviser ask", payload.question[:60])
        question, opts, context = build_ask_request(
            payload, provider_name, model=model)
        result = await ask_provider.ask(question, opts, context=context)
        result.answer = ("[online search returned no results — "
                         "offline answer]\n\n" + result.answer)
        return result
    if payload.screenshots:
        logger.warning("online-search ask: dropping %d screenshot(s) — "
                       "the search summarize flow is text-only",
                       len(payload.screenshots))
    context = chart_context_block(payload.plot_info, payload.theme_mode, 0)
    summary = await provider.summarize_hits_via_compose(
        payload.question, resp.hits, model=model,
        system_prompt=adviser_search_system_prompt(), engine=resp.engine,
        request_id=resp.request_id, created=resp.created, context=context)
    answer = summary.answer.strip()
    if not answer:
        raise LlmAskError(
            f"{provider.name}: online-search summary returned no content "
            f"(request_id={summary.request_id})")
    sources = _format_source_list(summary)
    if sources:
        answer = f"{answer}\n\n———\n参考来源：\n{sources}"
    logger.info("    [%s] online-search ask model=%s hits=%d cited=%s",
                summary.provider, summary.model, len(summary.hits),
                ",".join(summary.cited_refs) or "none")
    return AskResult(
        question=payload.question, answer=answer,
        provider=summary.provider, model=summary.model,
        request_id=summary.request_id, created=summary.created,
        usage=summary.usage)
