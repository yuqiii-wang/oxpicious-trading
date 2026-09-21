"""llm_agents.llm_ask.storage — persistence for interactive AI asks.

Every ``--payload-file`` ask (i.e. every data_viz "AI Ask" modal submit) is
appended to the ask-history tables so the interaction is searchable later,
mirroring how ``llm_agents.llm_qa`` stores the knowledge base:

  * ``text.llm_qa_by_ask``            — one row per ask (question, answer,
                                        model, code/product search keys).
  * ``text.llm_qa_ask_context``       — 1:1 per ask: the chart context the
                                        question was asked against (hot
                                        filter columns + verbatim plotInfo
                                        JSONB).
  * ``text.llm_qa_ask_context_images``— junction: one ask context -> N
                                        screenshots in
                                        ``multi_media.src_images`` (bytes
                                        deduplicated by content_hash).
  * ``text.llm_qa_keywords_by_ask``   — deterministic keyword rows derived
                                        from the payload (instrument
                                        codes/names, product/page, in-plot
                                        state values + searchKeywords,
                                        series names) — the keyword search
                                        index, like ``text.news_keywords``.

Canonical DDL: ``database/sql/text/05_llm_qa_by_ask.sql`` and
``database/sql/multi_media/*.sql``.

The whole write path is FAIL-SOFT: persistence must never fail the modal's
ask — any error (missing tables, DB down, malformed plot info) logs a
warning and the answer still travels back to the UI.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import logging
import struct
from typing import Dict, List, Optional, Tuple

from _common.db_commons import get_db_connection_async

from llm_agents._core.models import SHANGHAI_TZ
from llm_agents.llm_ask.core.models import AskResult
from llm_agents.llm_ask.payload import AiAskPayload

logger = logging.getLogger(__name__)

ASK_TABLE = "text.llm_qa_by_ask"
CONTEXT_TABLE = "text.llm_qa_ask_context"
CONTEXT_IMAGES_TABLE = "text.llm_qa_ask_context_images"
KEYWORDS_TABLE = "text.llm_qa_keywords_by_ask"
IMAGES_TABLE = "multi_media.src_images"

# Keyword cap — beyond this the extra terms are noise for keyword search,
# not signal (searchKeywords alone can carry 8).
MAX_KEYWORDS = 24
# Single keyword length cap — a state value/series name longer than this is
# prose, not a tag.
MAX_KEYWORD_LEN = 48


# ----------------------------------------------------------------------------
# Payload extraction — plot_info is external JSON, everything narrows
# defensively (a shape we don't understand degrades to fewer rows, never to
# an exception).
# ----------------------------------------------------------------------------

def _scope_instruments(plot_info: Dict[str, object]) -> List[Dict[str, object]]:
    scope = plot_info.get("scope")
    if not isinstance(scope, dict):
        return []
    instruments = scope.get("instruments")
    if not isinstance(instruments, list):
        return []
    return [i for i in instruments if isinstance(i, dict)]


def _primary_code(plot_info: Dict[str, object]) -> Optional[str]:
    """First scope instrument code — the ask's primary subject."""
    for inst in _scope_instruments(plot_info):
        code = inst.get("code")
        if isinstance(code, str) and code.strip():
            return code.strip()
    return None


def _product_tag(plot_info: Dict[str, object]) -> Optional[str]:
    """Product identity tag: the chart spec's product id, else the page path."""
    for key in ("product", "page"):
        value = plot_info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def derive_ask_tags(plot_info: Dict[str, object]) -> List[Tuple[str, str]]:
    """Deterministic ``(keyword, kind)`` rows for one ask's payload.

    Kinds: ``code`` (instrument codes), ``name`` (instrument display names),
    ``product`` (product id / page path), ``item`` (in-plot state values +
    searchKeywords — the active toggles/indicators), ``series`` (plotted
    series names). No LLM call and no segmentation — the same ask always
    derives the same keywords. Case-insensitively deduped, capped at
    MAX_KEYWORDS.
    """
    out: List[Tuple[str, str]] = []
    seen = set()

    def push(raw: object, kind: str) -> None:
        if not isinstance(raw, str):
            return
        value = raw.strip()
        if not value or len(value) > MAX_KEYWORD_LEN:
            return
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        out.append((value, kind))

    for inst in _scope_instruments(plot_info):
        push(inst.get("code"), "code")
        push(inst.get("name"), "name")
    push(_product_tag(plot_info), "product")

    state = plot_info.get("state")
    if isinstance(state, dict):
        for value in state.values():
            # Booleans are control noise, not searchable items.
            if isinstance(value, bool):
                continue
            push(value, "item")
    keywords = plot_info.get("searchKeywords")
    if isinstance(keywords, list):
        for kw in keywords:
            push(kw, "item")

    series = plot_info.get("series")
    if isinstance(series, list):
        for s in series:
            if isinstance(s, dict):
                push(s.get("name"), "series")

    return out[:MAX_KEYWORDS]


def _context_row(ask_id: int, payload: AiAskPayload,
                 n_images: int) -> Tuple[str, List[object]]:
    """Build the llm_qa_ask_context INSERT (sql, params) from a payload."""
    plot_info = payload.plot_info
    chart = plot_info.get("chart")
    if not isinstance(chart, dict):
        chart = {}
    scope = plot_info.get("scope")
    if not isinstance(scope, dict):
        scope = {}
    window = plot_info.get("window")
    if not isinstance(window, dict):
        window = {}

    def _text(value: object) -> Optional[str]:
        return value if isinstance(value, str) and value.strip() else None

    params: List[object] = [
        ask_id,
        _text(chart.get("kind")),
        _text(chart.get("title")),
        _text(chart.get("subtitle")),
        _text(chart.get("intro")),
        _text(scope.get("industry")),
        _text(scope.get("sector")),
        _text(window.get("start")),
        _text(window.get("end")),
        _text(window.get("granularity")),
        _text(plot_info.get("page")),
        payload.theme_mode,
        json.dumps(plot_info, ensure_ascii=False),
        n_images,
    ]
    sql = (
        f"INSERT INTO {CONTEXT_TABLE} (ask_id, chart_kind, chart_title, "
        f"chart_subtitle, chart_intro, industry_id, sector_id, window_start, "
        f"window_end, granularity, page, theme_mode, plot_info, n_images) "
        f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, "
        f"$13::jsonb, $14)"
    )
    return sql, params


# ----------------------------------------------------------------------------
# Screenshot decoding
# ----------------------------------------------------------------------------

def _decode_data_url(data_url: str) -> Tuple[str, bytes]:
    """Split a ``data:image/png;base64,…`` URL into (mime_type, bytes).

    Raises ValueError on a malformed URL (the caller skips that image).
    """
    if not data_url.startswith("data:"):
        raise ValueError("not a data URL")
    header, _, payload = data_url.partition(",")
    if not payload:
        raise ValueError("data URL has no payload")
    mime = header[5:].split(";", 1)[0] or "image/png"
    return mime, base64.b64decode(payload)


def _png_dims(data: bytes) -> Optional[Tuple[int, int]]:
    """(width, height) from the PNG IHDR chunk, None for non-PNG bytes.

    Layout: 8-byte signature, 4-byte chunk length, 'IHDR', then big-endian
    width and height.
    """
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


# ----------------------------------------------------------------------------
# Persist one ask
# ----------------------------------------------------------------------------

async def _check_tables(conn) -> bool:
    for table in (ASK_TABLE, CONTEXT_TABLE, CONTEXT_IMAGES_TABLE,
                  KEYWORDS_TABLE, IMAGES_TABLE):
        schema, name = table.split(".", 1)
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_schema = $1 AND table_name = $2)", schema, name)
        if not exists:
            logger.warning("ask persist: missing table %s — run "
                           "database/sql/multi_media/*.sql and "
                           "database/sql/text/05_llm_qa_by_ask.sql first",
                           table)
            return False
    return True


async def persist_ask(payload: AiAskPayload,
                      result: Optional[AskResult] = None,
                      error_tail: Optional[str] = None) -> Optional[int]:
    """Append one ask (and its context, images, keywords) to the history.

    ``result`` is the ``AskResult`` of a successful ask; when None the ask
    failed and a ``status='failed'`` row is written with *error_tail*.
    Returns the new ask_id, or None when persistence was skipped/failed —
    always fail-soft: the ask's answer must still reach the UI.
    """
    try:
        return await _persist_ask(payload, result, error_tail)
    except Exception:
        logger.warning("ask persist: failed (ask not stored, answering "
                       "unaffected)", exc_info=True)
        return None


async def _persist_ask(payload: AiAskPayload,
                      result: Optional[AskResult],
                      error_tail: Optional[str]) -> Optional[int]:
    answered = result is not None and bool(result.answer)

    plot_info = payload.plot_info
    code = _primary_code(plot_info)
    product = _product_tag(plot_info)
    ask_day = datetime.datetime.now(SHANGHAI_TZ).date()

    conn = await get_db_connection_async()
    try:
        if not await _check_tables(conn):
            return None
        async with conn.transaction():
            ask_id = await conn.fetchval(
                f"INSERT INTO {ASK_TABLE} (question, answer, status, "
                f"error_tail, online_search, search_query, provider, "
                f"llm_model, code, product) "
                f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
                f"RETURNING ask_id",
                payload.question,
                result.answer if answered else None,
                "answered" if answered else "failed",
                error_tail,
                payload.online_search,
                payload.search_query,
                result.provider if answered else None,
                result.model if answered else None,
                code,
                product,
            )

            sql, params = _context_row(ask_id, payload,
                                       n_images=len(payload.screenshots))
            await conn.execute(sql, *params)

            for position, data_url in enumerate(payload.screenshots):
                try:
                    mime, data = _decode_data_url(data_url)
                except ValueError:
                    logger.warning("ask persist: skipping malformed "
                                   "screenshot %d", position)
                    continue
                dims = _png_dims(data)
                image_id = await conn.fetchval(
                    f"INSERT INTO {IMAGES_TABLE} (mime_type, byte_size, "
                    f"width, height, content_hash, label, data) "
                    f"VALUES ($1, $2, $3, $4, $5, $6, $7) "
                    # DO UPDATE (not DO NOTHING) so RETURNING always yields
                    # the id, also for an already-stored identical screenshot.
                    f"ON CONFLICT (content_hash) DO UPDATE "
                    f"SET label = EXCLUDED.label RETURNING image_id",
                    mime, len(data),
                    dims[0] if dims else None, dims[1] if dims else None,
                    hashlib.sha256(data).hexdigest(),
                    f"ai-ask screenshot {position + 1}/{len(payload.screenshots)}",
                    data,
                )
                await conn.execute(
                    f"INSERT INTO {CONTEXT_IMAGES_TABLE} (ask_id, image_id, "
                    f"position) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                    ask_id, image_id, position)

            keywords = derive_ask_tags(plot_info)
            if keywords:
                await conn.executemany(
                    f"INSERT INTO {KEYWORDS_TABLE} (ask_id, keyword, kind, "
                    f"code, product, ask_day) VALUES ($1, $2, $3, $4, $5, "
                    f"$6) ON CONFLICT DO NOTHING",
                    [(ask_id, kw, kind, code, product, ask_day)
                     for kw, kind in keywords])

        logger.info("ask persist: stored ask_id=%s status=%s keywords=%d "
                    "images=%d code=%s product=%s",
                    ask_id, "answered" if answered else "failed",
                    len(keywords), len(payload.screenshots), code, product)
        return ask_id
    finally:
        await conn.close()
