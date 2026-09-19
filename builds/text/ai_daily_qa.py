"""builds.text.ai_daily_qa — Persist the ai_daily Q&A into text.llm_qa.

The DB half of the ai_daily flow since the ``--store`` removal from
``downloads.macro.ai_daily``: the downloader writes ARTIFACTS ONLY
(temps/ai_daily/*.json), and this step — run inside ``python -m
builds.text`` after the text.news upsert — stores each envelope's Q&A
into ``text.llm_qa`` with its provenance:

  * text.news_groups + text.news_group_items — the ask's reference
    articles grouped as provenance (llm_agents.llm_qa.ensure_news_group);
  * text.llm_qa — via llm_agents.llm_qa.upsert_qa (idempotent on
    (question, news_group_id); NULL-group rows matched explicitly), the
    formatted reference list as ``context``, category ``market_daily``
    and ``qa_date`` pinned to the ask's data date (target_date /
    movers plan_date, Asia/Shanghai midnight);
  * text.llm_qa_refs — one row per RESOLVED ARTICLE (PK (qa_id, news_id),
    representative ref tag, ref_type ``exact`` / resolved_via
    ``summary``), news_id resolved against the text.news rows the
    ai_daily LOADER wrote from the same hits (shared natural-key
    computation in builds.text.loaders.ai_daily.hit_news_row); hits with
    no loader row land anyway, each as its own text.news placeholder row
    built from the artifact's echo metadata, and ``is_used`` marks the
    references the answer actually cites (extract_cited_refs) — every
    reference loads, cited or not.

Deliberately NETWORK-FREE — unlike the llm_agents ``--store`` path there
is no per-ref corpus/ddgs re-resolution: the artifact's own hits are the
truth, exactly what the downloader received. Envelopes already stored
are skipped by (question, qa_date) so re-runs (and artifacts a --store
run stored before the removal) never duplicate llm_qa rows.

Movers asks of artifacts written BEFORE the movers references embedding
carry no hits — they store without provenance; those days were already
stored by --store runs, so in practice only skips hit that path.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from _common.build_commons import bulk_upsert_async
from builds.text.loaders._parsing import parse_date_str
from builds.text.loaders.ai_daily import iter_ai_daily_envelopes
from llm_agents.llm_qa import upsert_qa
from llm_agents.online_search_summary import DAILY_CATEGORY
from llm_agents.online_search_summary.core.citations import (
    extract_cited_refs, format_references, normalize_refer,
)
from llm_agents.online_search_summary.core.models import (
    SHANGHAI_TZ, SearchHit,
)
from llm_agents.online_search_summary.storage.matching import (
    REF_TYPE_EXACT, VIA_SUMMARY, hit_ref_time,
)
from llm_agents.online_search_summary.storage.resolve import (
    ResolvedRef, build_ref_rows,
)

logger = logging.getLogger(__name__)

QA_TABLE = "text.llm_qa"
REFS_TABLE = "text.llm_qa_refs"

# Stored under the market_daily category — the same value the removed
# --store path (llm_agents.online_search_summary presets) used, so rows
# written before AND after the removal share one category.
LANGUAGE = "zh"


@dataclass
class _QaDoc:
    """One storable Q&A (the general summary or one movers ask)."""
    question: str
    answer: str
    qa_date: datetime.datetime          # the ask's data date, SH midnight
    industry_id: Optional[str]          # None -> broad-market tag at sync
    hits: List[SearchHit]
    llm_model: Optional[str]


def _hit(d: Dict[str, Any]) -> Optional[SearchHit]:
    """Rebuild a SearchHit from an artifact reference dict.

    publish_date is derived from the raw string alone (the artifacts store
    publish_date: null for slash-form raws like 2026/09/14) so the text.news
    natural key and ref_time agree with what the loader wrote from the same
    hit.
    """
    if not isinstance(d, dict):
        return None
    title = (d.get("title") or "").strip()
    if not title:
        return None
    return SearchHit(
        refer=d.get("refer") or "",
        title=title,
        content=d.get("content"),
        link=d.get("link"),
        media=d.get("media"),
        icon=d.get("icon"),
        publish_date=parse_date_str(d.get("publish_date_raw")),
        publish_date_raw=d.get("publish_date_raw"),
    )


def _midnight(d: datetime.date) -> datetime.datetime:
    return datetime.datetime.combine(d, datetime.time.min, tzinfo=SHANGHAI_TZ)


def _build_docs() -> List[_QaDoc]:
    """One _QaDoc per general summary + per movers ask across artifacts."""
    docs: List[_QaDoc] = []
    for _path, data, is_movers_file, file_date in iter_ai_daily_envelopes():
        if not is_movers_file:
            question = (data.get("question") or "").strip()
            answer = (data.get("answer") or "").strip()
            if question and answer:
                docs.append(_QaDoc(
                    question=question, answer=answer,
                    qa_date=_midnight(file_date), industry_id=None,
                    hits=[h for h in map(_hit, data.get("references") or [])
                          if h is not None],
                    llm_model=data.get("model")))

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
            docs.append(_QaDoc(
                question=question, answer=answer,
                qa_date=_midnight(plan_date),
                industry_id=(asked.get("industry_id") or "").strip() or None,
                hits=[h for h in map(_hit, asked.get("references") or [])
                      if h is not None],
                llm_model=asked.get("model")))
    return docs


async def _stored_qa_keys(conn) -> set:
    """{(question, qa_day)} already in text.llm_qa for this flow's category."""
    rows = await conn.fetch(
        f'SELECT question, '
        f'(qa_date AT TIME ZONE \'Asia/Shanghai\')::date AS qa_day '
        f'FROM {QA_TABLE} WHERE category = $1', DAILY_CATEGORY)
    return {(r["question"], r["qa_day"]) for r in rows}


def hit_news_key(
    h: SearchHit,
    fallback_date: datetime.date,
) -> Tuple[str, str, datetime.date]:
    """The text.news natural key of a hit — byte-identical to the key of the
    row builds.text.loaders.ai_daily.hit_news_row wrote for it: (stripped
    title, media or 'web', raw-date or the envelope's date)."""
    return ((h.title or "").strip(),
            (h.media or "").strip() or "web",
            h.publish_date or fallback_date)


async def _resolve_hits(
    conn,
    doc: _QaDoc,
    *,
    industry_id: Optional[str],
) -> Tuple[List[int], List[ResolvedRef]]:
    """Resolve the ask's hits to (group news_ids, ResolvedRefs).

    Every hit the ai_daily loader stored resolves (shared natural key).
    Misses are NOT dropped: each is stored as its own text.news row (the
    artifact's echo metadata, network-free — see _store_misses) so
    text.llm_qa_refs keeps every reference. Group ids are news_id
    first-occurrence order, mirroring the --store path.
    """
    if not doc.hits:
        return [], []
    # Pre-embedding artifacts carry an empty refer, bare-index refers also
    # occur — canonicalize to ref_N so the stored tag stays addressable.
    for i, h in enumerate(doc.hits):
        h.refer = normalize_refer(h.refer, i)
    keys = [hit_news_key(h, doc.qa_date.date()) for h in doc.hits]
    rows = await conn.fetch(
        'SELECT news_id, title, source, date FROM text.news '
        'WHERE (title, source, date) IN ('
        '  SELECT * FROM unnest($1::text[], $2::text[], $3::date[]))',
        [k[0] for k in keys], [k[1] for k in keys], [k[2] for k in keys])
    id_by_key = {(r["title"], r["source"], r["date"]): r["news_id"]
                 for r in rows}

    resolved: List[ResolvedRef] = []
    misses: List[SearchHit] = []
    for h, key in zip(doc.hits, keys):
        news_id = id_by_key.get(key)
        if news_id is None:
            misses.append(h)
        else:
            resolved.append(ResolvedRef(h.refer, REF_TYPE_EXACT, news_id,
                                        h.link, hit_ref_time(h),
                                        via=VIA_SUMMARY))
    if misses:
        resolved.extend(await _store_misses(conn, doc, misses,
                                            industry_id=industry_id))
    group_ids: List[int] = []
    seen: set = set()
    for r in resolved:
        if r.news_id not in seen:
            seen.add(r.news_id)
            group_ids.append(r.news_id)
    return group_ids, resolved


async def _store_misses(
    conn,
    doc: _QaDoc,
    misses: List[SearchHit],
    *,
    industry_id: Optional[str],
) -> List[ResolvedRef]:
    """Store each unmatched hit as its own text.news row; return its
    ResolvedRefs.

    A hit with no loader row is still a reference the answer was served,
    so text.llm_qa_refs keeps it instead of dropping it. NETWORK-FREE:
    the row carries the artifact's own echo metadata (snippet content
    when present, like the loader's rows) and can be re-resolved in
    place by a later run.
    """
    fallback_date = doc.qa_date.date()
    from builds.text.keywords import get_taxonomy
    taxonomy = get_taxonomy()
    seen: set = set()
    news_rows: List[Dict[str, Any]] = []
    for h in misses:
        source = (h.media or "").strip() or "web"
        date = h.publish_date or fallback_date
        key = (h.title, source, date)
        if key in seen:          # one row per article, several tags may hit it
            continue
        seen.add(key)
        text = h.title + "\n" + (h.content or "")
        ind = industry_id
        if ind is None:
            _, ind = taxonomy.match(text)
        news_rows.append({
            "title": h.title, "content": h.content, "date": date,
            "source": source, "url": h.link, "author": None,
            "industry_id": ind, "sector_id": taxonomy.sector_of(ind),
            "word_count": taxonomy.word_count(text) if h.content else None,
            "votes": None,
        })
    if not news_rows:
        return []
    await bulk_upsert_async(conn, "text.news", news_rows,
                            ["title", "source", "date"])
    rows = await conn.fetch(
        'SELECT news_id, title, source, date FROM text.news '
        'WHERE (title, source, date) IN ('
        '  SELECT * FROM unnest($1::text[], $2::text[], $3::date[]))',
        [r["title"] for r in news_rows], [r["source"] for r in news_rows],
        [r["date"] for r in news_rows])
    id_by_key = {(r["title"], r["source"], r["date"]): r["news_id"]
                 for r in rows}
    out: List[ResolvedRef] = []
    for h in misses:
        key = (h.title, (h.media or "").strip() or "web",
               h.publish_date or fallback_date)
        news_id = id_by_key.get(key)
        if news_id is None:
            logger.error("    [DB] %s: placeholder news row re-find failed "
                         "for %r", __name__, h.title[:60])
            continue
        out.append(ResolvedRef(h.refer, REF_TYPE_EXACT, news_id, h.link,
                               hit_ref_time(h), via=VIA_SUMMARY))
    return out


async def sync_ai_daily_qa(conn) -> Tuple[int, int]:
    """Store every not-yet-stored ai_daily Q&A; return (stored, skipped).

    Idempotent: a (question, qa_date) already in text.llm_qa (category
    market_daily — including rows the removed --store path wrote) is
    skipped, so the build can re-run freely over growing artifact
    history.
    """
    docs = _build_docs()
    if not docs:
        return 0, 0
    stored = await _stored_qa_keys(conn)
    # Broad-market tag for the general summary (the removed --store flow
    # pinned the same tag; movers asks carry their own industry_id).
    from downloads.macro.ai_daily import movers
    broad = await movers.broad_industry_id(conn)
    from builds.text.keywords import get_taxonomy
    taxonomy = get_taxonomy()

    n_stored = n_skipped = 0
    for doc in docs:
        key = (doc.question, doc.qa_date.date())
        if key in stored:
            n_skipped += 1
            continue
        industry_id = doc.industry_id if doc.industry_id is not None else broad
        group_ids, resolved = await _resolve_hits(conn, doc,
                                                  industry_id=industry_id)
        # is_used per ref: the answer's own citation tags (the LLM-answer
        # half of the search/summary separation — refs load regardless).
        cited = set(extract_cited_refs(doc.answer))
        qa_id = await upsert_qa(
            conn, doc.question, doc.answer,
            context=format_references(doc.hits) or None,
            category=DAILY_CATEGORY, industry_id=industry_id,
            sector_id=taxonomy.sector_of(industry_id),
            news_ids=group_ids or None, llm_model=doc.llm_model,
            language=LANGUAGE, qa_date=doc.qa_date)
        n_refs = 0
        if resolved:
            # PK (qa_id, news_id): re-sync refreshes the resolution AND
            # the is_used typing in place.
            ref_rows = build_ref_rows(qa_id, resolved, cited=cited)
            n_refs = len(ref_rows)
            await bulk_upsert_async(conn, REFS_TABLE, ref_rows,
                                    ["qa_id", "news_id"])
        # has_refs mirrors the table state (see the store's write path).
        has_refs = await conn.fetchval(
            f'SELECT EXISTS (SELECT 1 FROM {REFS_TABLE} WHERE qa_id = $1)',
            qa_id)
        await conn.execute(
            f'UPDATE {QA_TABLE} SET has_refs = $2 WHERE qa_id = $1',
            qa_id, has_refs)
        stored.add(key)
        n_stored += 1
        logger.info("    [DB] ai_daily Q&A stored qa_id=%s (%s) refs=%d",
                    qa_id, doc.question[:40], n_refs)
    return n_stored, n_skipped
