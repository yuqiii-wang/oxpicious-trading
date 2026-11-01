"""llm_agents.llm_qa_by_research.generation — the six-request research flow.

``generate_research_qa`` produces ONE text.llm_qa_by_research row from
SIX separate LLM requests (the generation contract in
database/sql/text/06_llm_qa_by_research.sql):

  * requests 1-5 — one per evaluation dimension
    (:data:`~llm_agents.llm_qa_by_research.prompts.DIMENSIONS`): a
    dedicated web search per dimension, then a composed answer over the
    hits (``summarize_hits_via_compose``; plain ``_chat_complete`` when
    the search surfaced nothing). Each reply lands in its own column —
    intro / optimistic_forecast / pessimistic_forecast / risks /
    capitalization. A failed dimension request
    is ISOLATED (movers-style): the column stays NULL, the flow
    continues.
  * request 6 — a strict-JSON summary over the five dimension texts:
    the summary lands in ``answer`` (replacing any whole-answer
    semantics — the row's display content) and the logic_label is
    assigned from the parsed vocabulary name. One parse retry; a final
    failure raises (a row without its summary + label is not
    displayable, so nothing is stored).

    REFS MAPPING (re-arranged for the six-request shape): every dimension
request's hits are fast-resolved into text.news rows right after the
search (the ``OnlineSearchSummaryStore`` no-network path — the rows ARE
the search echoes) and the resolved ids are BOUND around the compose
request, so each dimension's text.llm_requests row carries its
references as context_ref_ids. The resolved refs are interest-window
filtered at store time and MERGED into ONE news group linked from the
row (the by-research analog of text.llm_qa_refs, which FKs text.llm_qa
only); ``context`` keeps the per-dimension reference blocks so each
dimension answer's inline [来源：ref_N] citations stay aligned with its
own block. ``has_refs`` mirrors the group afterwards (the text.llm_qa
store pattern).
"""
from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from _common.labels import RESEARCH_LOGIC_CODE
from llm_agents._core.request_log import bind_ref_ids, bind_request_chain
from llm_agents.online_search_summary.core.citations import (
    extract_cited_refs, format_references,
)
from llm_agents.online_search_summary.core.models import (
    SHANGHAI_TZ, SearchHit, SearchOptions, SearchSummary,
)
from llm_agents.online_search_summary.providers.base import (
    BaseOnlineSearchProvider,
)
from llm_agents.online_search_summary.storage.matching import (
    REF_WINDOW_DAYS, in_ref_window,
)
from llm_agents.online_search_summary.storage.resolve import ResolvedRef
from llm_agents.online_search_summary.storage.store import OnlineSearchSummaryStore

from .prompts import (
    DIMENSIONS, DimensionSpec, SummaryParseError, build_summary_user_message,
    dimension_plain_user_message, dimension_question,
    dimension_search_query, dimension_system_prompt, parse_summary_json,
    summary_system_prompt,
)
from .storage import set_has_refs, upsert_qa_by_research

logger = logging.getLogger(__name__)

DEFAULT_CATEGORY = "research"
DEFAULT_SEARCH_COUNT = 8


@dataclass
class DimensionResult:
    """One dimension request's outcome (text None = the request failed).

    ``resolved`` carries the dimension's hits fast-resolved into
    text.news rows (the request-time resolution that also binds the
    text.llm_requests ref ids); empty when the search surfaced nothing
    or resolution failed (refs stay non-fatal).
    """
    spec: DimensionSpec
    text: Optional[str] = None
    hits: List[SearchHit] = field(default_factory=list)
    resolved: List[ResolvedRef] = field(default_factory=list)


async def _request_dimension(
    provider: BaseOnlineSearchProvider,
    spec: DimensionSpec,
    subject: str,
    *,
    model: str,
    lang: str,
    search_count: int,
    recency: str,
    conn,
    industry_id: Optional[str],
) -> DimensionResult:
    """One dimension request: search, resolve the hits to text.news ids,
    then compose over them WITH the ids bound (the text.llm_requests
    source-log contract — or a plain no-hits completion when the search
    surfaced nothing). Transport/API retries are the provider's; refs
    resolution failure is non-fatal (the request runs unbound)."""
    query = dimension_search_query(spec, subject)
    resp = await provider.search(
        query, SearchOptions(count=search_count, recency=recency))
    if not resp.hits:
        # No references surfaced — answer from the model's own knowledge.
        reply, _usage = await provider._chat_complete(
            [{"role": "system",
              "content": dimension_system_prompt(spec, lang)},
             {"role": "user",
              "content": dimension_plain_user_message(spec, subject)}],
            model=model)
        return DimensionResult(spec, text=reply)
    resolved: List[ResolvedRef] = []
    try:
        resolved = await OnlineSearchSummaryStore(conn)._fast_resolve(
            resp.hits, industry_id=industry_id)
    except Exception as e:  # refs resolution is non-fatal
        logger.warning("    [DB] %s refs resolution failed (request runs "
                       "unbound): %s", spec.key, e)
    async def compose() -> SearchSummary:
        return await provider.summarize_hits_via_compose(
            dimension_question(spec, subject), resp.hits,
            model=model, system_prompt=dimension_system_prompt(spec, lang),
            lang=lang, engine=resp.engine, request_id=resp.request_id,
            created=resp.created)

    if resolved:
        seen: set = set()
        news_ids = [r.news_id for r in resolved
                    if not (r.news_id in seen or seen.add(r.news_id))]
        with bind_ref_ids(news_ids):
            summary = await compose()
    else:
        summary = await compose()
    return DimensionResult(spec, text=summary.answer, hits=resp.hits,
                           resolved=resolved)


async def _summarize_and_label(
    provider: BaseOnlineSearchProvider,
    subject: str,
    results: List[DimensionResult],
    *,
    model: str,
    lang: str,
) -> Tuple[str, str]:
    """Request 6: the strict-JSON summary + logic_label over the five
    dimension texts. ONE parse retry with a stricter instruction appended
    (the news_digestions pattern); transport retries stay in the provider."""
    messages: List[dict] = [
        {"role": "system", "content": summary_system_prompt(lang)},
        {"role": "user", "content": build_summary_user_message(
            subject, [(r.spec, r.text) for r in results
                      if r.text is not None])},
    ]
    last_err: Optional[SummaryParseError] = None
    for attempt in (1, 2):
        reply, _usage = await provider._chat_complete(messages, model=model)
        try:
            return parse_summary_json(reply)
        except SummaryParseError as e:
            last_err = e
            logger.warning("    [LLM] summary parse attempt %d failed: %s",
                           attempt, e)
            messages = messages + [
                {"role": "user",
                 "content": "上一次回复无法解析。请严格只输出一个 JSON 对象"
                            '{"summary": "…", "logic_label": "…"}，'
                            "不要包含任何其他文字或代码块标记。"},
            ]
    raise last_err  # type: ignore[misc]


async def generate_research_qa(
    conn,
    provider: BaseOnlineSearchProvider,
    question: str,
    *,
    industry_id: Optional[str] = None,
    sector_id: Optional[str] = None,
    model: Optional[str] = None,
    lang: str = "zh",
    category: Optional[str] = DEFAULT_CATEGORY,
    search_count: int = DEFAULT_SEARCH_COUNT,
    recency: str = "noLimit",
    qa_date: Optional[datetime.datetime] = None,
) -> int:
    """Run the six-request research flow and store the row; return qa_id.

    The five dimension requests run sequentially (each = one search + one
    chat call); a failed dimension is logged and skipped (its column
    stays NULL). The 6th request's failure raises AFTER the provider's
    own retries plus one parse retry — nothing is stored in that case
    (re-run ``generate``; the searches are re-issued).
    """
    model = model or getattr(provider, "DEFAULT_MODEL", None)
    if not model:
        raise ValueError(f"{provider.name}: no chat model configured")

    flow_start = datetime.datetime.now(datetime.timezone.utc)

    # The six requests form ONE chain (text.llm_requests chain_id /
    # prev_request_id): from the second request on, the logged payload is
    # incremental to the previous request's (shared system prompts and
    # context blocks stored once, in the first row).
    with bind_request_chain():
        return await _generate_research_qa_chained(
            conn, provider, question,
            industry_id=industry_id, sector_id=sector_id, model=model,
            lang=lang, category=category, search_count=search_count,
            recency=recency, qa_date=qa_date, flow_start=flow_start)


async def _generate_research_qa_chained(
    conn,
    provider: BaseOnlineSearchProvider,
    question: str,
    *,
    industry_id: Optional[str],
    sector_id: Optional[str],
    model: Optional[str],
    lang: str,
    category: Optional[str],
    search_count: int,
    recency: str,
    qa_date: Optional[datetime.datetime],
    flow_start: datetime.datetime,
) -> int:
    """The flow body (runs inside the request chain binding)."""

    # -- requests 1-5: the five evaluation dimensions ------------------------
    results: List[DimensionResult] = []
    for spec in DIMENSIONS:
        try:
            r = await _request_dimension(
                provider, spec, question,
                model=model, lang=lang, search_count=search_count,
                recency=recency, conn=conn, industry_id=industry_id)
            logger.info("    [LLM] %s: %d chars, %d hit(s)",
                        spec.key, len(r.text or ""), len(r.hits))
        except Exception as e:  # isolated per-dimension failure
            logger.warning("    [LLM] %s request FAILED (column stays "
                           "NULL): %s", spec.key, e)
            r = DimensionResult(spec)
        results.append(r)
    if all(r.text is None for r in results):
        raise RuntimeError(
            f"all five dimension requests failed for {question!r} — "
            "nothing to summarize; not storing")

    # -- request 6: summary + logic_label -----------------------------------
    summary_text, label_name = await _summarize_and_label(
        provider, question, results, model=model, lang=lang)
    logger.info("    [LLM] summary: %d chars, logic_label=%s",
                len(summary_text), label_name)

    # -- request-id collection: the source-log backlink ----------------------
    # All 6 requests are done; drain the recorder, then match THIS flow's
    # chat-completions rows (service + flow window — the web-search POSTs
    # are excluded by endpoint). Non-fatal: an empty list stores without
    # the linkage.
    request_ids: List[int] = []
    try:
        from llm_agents._core.request_log import (
            fetch_recent_request_ids, flush,
        )
        flush(15)
        request_ids = await fetch_recent_request_ids(conn, flow_start)
    except Exception as e:  # noqa: BLE001 — linkage is non-fatal
        logger.warning("    [DB] request-id collection failed (storing "
                       "without source-log linkage): %s", e)

    # -- refs mapping: merged news group of every dimension's hits ----------
    qa_id = await _store_research_row(
        conn, question, summary_text, label_name, results,
        model=model, lang=lang, category=category,
        industry_id=industry_id, sector_id=sector_id, qa_date=qa_date,
        request_ids=request_ids)
    return qa_id


async def _store_research_row(
    conn,
    question: str,
    summary_text: str,
    label_name: str,
    results: List[DimensionResult],
    *,
    model: Optional[str],
    lang: str,
    category: Optional[str],
    industry_id: Optional[str],
    sector_id: Optional[str],
    qa_date: Optional[datetime.datetime],
    request_ids: Optional[List[int]] = None,
) -> int:
    """Persist the row: merge the REQUEST-TIME-resolved refs of every
    dimension into ONE news group (the refs mapping — resolution already
    happened in _request_dimension, where it also bound the
    text.llm_requests ref ids), store the per-dimension reference blocks
    as context, then mirror has_refs."""
    all_resolved: List[ResolvedRef] = [rr for r in results for rr in r.resolved]
    group_ids: List[int] = []
    if all_resolved:
        qa_day = (qa_date or datetime.datetime.now(SHANGHAI_TZ)).date()
        kept = [r for r in all_resolved if in_ref_window(r.ref_time, qa_day)]
        n_dropped = len(all_resolved) - len(kept)
        if n_dropped:
            logger.info("    [DB] %d ref(s) outside the qa interest "
                        "window (qa_day=%s, -%dd) dropped",
                        n_dropped, qa_day.isoformat(), REF_WINDOW_DAYS)
        seen: set = set()
        for r in kept:  # dedupe in ref order (two dims may share an article)
            if r.news_id not in seen:
                seen.add(r.news_id)
                group_ids.append(r.news_id)
    else:
        logger.warning("    [DB] no resolved search refs across the five "
                       "dimension requests — storing without news-group "
                       "provenance")

    context_blocks = [
        f"【{r.spec.title}】\n{format_references(r.hits)}"
        for r in results if r.hits
    ]
    # Per-dimension ref resolution for the UI's expandable [来源：ref_N]
    # tags (the text.llm_qa_refs pattern, mirrored as a JSONB column —
    # text.llm_qa_refs FKs text.llm_qa only). is_used = the dimension's
    # stored answer cites the tag.
    dim_refs: Dict[str, List[dict]] = {}
    for r in results:
        if not r.resolved:
            continue
        cited = set(extract_cited_refs(r.text or ""))
        dim_refs[r.spec.key] = [
            {"ref": rr.ref, "news_id": rr.news_id,
             "is_used": rr.ref in cited,
             "ref_time": (rr.ref_time.isoformat() if rr.ref_time else None)}
            for rr in r.resolved
        ]
    if sector_id is None and industry_id:
        from builds.text.keywords import get_taxonomy
        sector_id = get_taxonomy().sector_of(industry_id)

    by_key = {r.spec.key: r for r in results}
    qa_id = await upsert_qa_by_research(
        conn, question, summary_text, RESEARCH_LOGIC_CODE[label_name],
        intro=by_key["intro"].text,
        optimistic_forecast=by_key["optimistic_forecast"].text,
        pessimistic_forecast=by_key["pessimistic_forecast"].text,
        risks=by_key["risks"].text,
        capitalization=by_key["capitalization"].text,
        context="\n\n".join(context_blocks) or None,
        category=category, industry_id=industry_id, sector_id=sector_id,
        news_ids=group_ids or None,
        request_ids=request_ids or None,
        dim_refs=json.dumps(dim_refs) if dim_refs else None,
        llm_model=model, language=lang, qa_date=qa_date)
    if group_ids:
        await set_has_refs(conn, qa_id, True)
    logger.info("    [DB] stored text.llm_qa_by_research qa_id=%s "
                "(sources=%d, label=%s)", qa_id, len(group_ids), label_name)
    return qa_id
