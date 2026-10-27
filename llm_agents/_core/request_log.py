"""llm_agents._core.request_log — source LLM request/response recorder.

Appends one row per logical LLM API call to ``text.llm_requests``
(database/sql/text/07_llm_requests.sql): the full request payload and
the FINAL attempt's response payload as JSONB, the coarse outcome
(status / is_failed / failure_reason / duration_ms — the same grain as
``monitor.external_resource_audit``), and the request's context /
reference SOURCE ids so readers load the source data by ref ids (a join
against ``text.news`` / ``text.news_groups``) instead of re-parsing
payloads.

Hooked from exactly one place — ``transport.RetryingJsonClient.post_json``
— the JSON POST transport every llm_agents provider shares, so llm_ask /
online_search_summary / news_digestions / llm_qa_by_research are all
covered without per-agent plumbing.

Ref-id binding: an agent that knows which source articles feed the
request wraps the call in::

    with bind_ref_ids(article["news_id"]):
        reply, _ = await provider._chat_complete(messages, model=model)

The binding is a ``contextvars.ContextVar`` — it survives the
``asyncio.to_thread`` hop the async transport makes, and concurrent
requests each see their own binding. news_digestions binds its digest
source article; flows whose news ids only resolve at store time (the
search-summary resolution) leave the ids empty.

Never-blocks contract: identical in spirit to
``_common/external_resource_audit.py`` (bounded queue, one lazy daemon
writer thread, batched executemany over a persistent autocommit
connection, DB outages drop rows instead of perturbing requests). The
service name resolves through the audit module's resolver (imported, not
duplicated). db_commons is imported LAZILY inside the writer thread (the
cudf.pandas activation-order rule).
"""
from __future__ import annotations

import contextlib
import json
import queue
import threading
import time
from contextvars import ContextVar
from typing import Any, Dict, Iterable, List, Optional, Sequence

# Payload hygiene caps: a base64 screenshot is not source data worth
# storing; failure reasons stay short like the audit's.
_DATA_URL_PREFIX = "data:image/"
_DATA_URL_PLACEHOLDER = "[screenshot omitted]"
_REASON_MAX = 500
_QUEUE_MAX = 2000
_BATCH_MAX = 100
_WARN_EVERY_S = 1800.0

# (news_ids tuple, news_group_id, qa_id) bound to the current context.
_refs: ContextVar[Optional[tuple]] = ContextVar(
    "llm_request_refs", default=None)

_queue: "queue.Queue[tuple]" = queue.Queue(maxsize=_QUEUE_MAX)
_writer_lock = threading.Lock()
_writer: Optional[threading.Thread] = None
_last_warn = 0.0


@contextlib.contextmanager
def bind_ref_ids(news_ids: Iterable[int] = (), *,
                 news_group_id: Optional[int] = None,
                 qa_id: Optional[int] = None):
    """Context manager binding the request's source ref ids.

    The transport reads the binding at record time and writes it to
    ``context_ref_ids`` / ``news_group_id`` / ``qa_id``. Nesting merges:
    an inner binding keeps the outer news ids (order preserved, deduped)
    unless it declares its own.
    """
    outer = _refs.get()
    ids = [int(n) for n in news_ids]
    if not ids and outer is not None:
        ids = list(outer[0])
    seen: set = set()
    merged = [n for n in ids if not (n in seen or seen.add(n))]
    group = news_group_id
    qa = qa_id
    if outer is not None:
        if group is None:
            group = outer[1]
        if qa is None:
            qa = outer[2]
    token = _refs.set((tuple(merged), group, qa))
    try:
        yield
    finally:
        _refs.reset(token)


def current_refs() -> tuple:
    """The context's bound ``(news_ids, news_group_id, qa_id)``."""
    bound = _refs.get()
    return bound if bound is not None else ((), None, None)


def _sanitize(value: Any) -> Any:
    """Copy *value* replacing data-URL strings with a short placeholder."""
    if isinstance(value, str):
        if value.startswith(_DATA_URL_PREFIX):
            return _DATA_URL_PLACEHOLDER
        return value
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return value


def _lean_url(url: str) -> str:
    """scheme://host/path — query strings are noise (the audit's rule)."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}{parts.path}"
    except ValueError:
        return url


def _warn(msg: str) -> None:
    global _last_warn
    now = time.monotonic()
    if now - _last_warn < _WARN_EVERY_S:
        return
    _last_warn = now
    print(f"[llm_request_log] {msg}", flush=True)


def record_request(provider: str, url: str, payload: Dict[str, Any],
                   *, model: Optional[str] = None,
                   data: Optional[Dict[str, Any]] = None,
                   status_code: Optional[int] = None,
                   is_failed: bool = False,
                   failure_reason: Optional[str] = None,
                   duration_ms: Optional[float] = None) -> None:
    """Enqueue one ``text.llm_requests`` row. Never raises, never blocks.

    Called from the transport's post_json with the request payload and
    the final attempt's outcome; *data* is the parsed response body
    (None on failure). The ref-id binding of the calling context rides
    along.
    """
    news_ids, group_id, qa_id = current_refs()
    row = (
        _resolve_service(), provider,
        model or (payload.get("model") if isinstance(payload, dict) else None),
        _lean_url(url),
        json.dumps(_sanitize(payload), ensure_ascii=False),
        json.dumps(_sanitize(data), ensure_ascii=False) if data is not None else None,
        status_code, is_failed,
        (failure_reason[:_REASON_MAX] if failure_reason else None),
        (round(duration_ms) if duration_ms is not None else None),
        list(news_ids), group_id, qa_id,
    )
    try:
        _queue.put_nowait(row)
    except queue.Full:
        _warn("queue full — dropping request-log rows (writer wedged or DB down?)")
        return
    _ensure_writer()


def _resolve_service() -> str:
    """The audit module's resolver — imported, never duplicated."""
    from _common.external_resource_audit import _resolve_service
    return _resolve_service()


_INSERT_SQL = """
INSERT INTO text.llm_requests
    (service, provider, model, endpoint, request_payload,
     response_payload, status_code, is_failed, failure_reason,
     duration_ms, context_ref_ids, news_group_id, qa_id)
VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
"""


def flush(timeout_s: float = 5.0) -> bool:
    """Block until the queue is drained (tests / graceful shutdown).
    Returns True when the queue emptied within *timeout_s*. The trailing
    grace covers the writer's in-flight batch."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not _queue.empty():
        time.sleep(0.05)
    if not _queue.empty():
        return False
    time.sleep(0.3)
    return _queue.empty()


def _ensure_writer() -> None:
    global _writer
    if _writer is not None and _writer.is_alive():
        return
    with _writer_lock:
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(
                name="llm-request-log", target=_writer_loop, daemon=True)
            _writer.start()


def _writer_loop() -> None:
    # db_commons (and its transitive pandas import) stays inside the
    # writer thread — the cudf.pandas activation-order rule.
    from _common.db_commons import get_db_connection

    conn = None
    while True:
        try:
            row = _queue.get(timeout=0.5)
        except queue.Empty:
            continue
        batch = [row]
        while len(batch) < _BATCH_MAX:
            try:
                batch.append(_queue.get_nowait())
            except queue.Empty:
                break
        if conn is None:
            try:
                conn = get_db_connection()
            except Exception as e:  # noqa: BLE001 — never kill the writer
                _warn(f"connect failed, dropping {len(batch)} row(s): {e}")
                continue
        try:
            with conn.cursor() as cur:
                cur.executemany(_INSERT_SQL, batch)
        except Exception as e:  # noqa: BLE001 — drop the batch, reconnect
            _warn(f"insert failed, dropping {len(batch)} row(s): {e}")
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn = None


async def fetch_recent_request_ids(
        conn, since, *, endpoint_suffix: str = "/chat/completions",
) -> List[int]:
    """This process's request-log ids created since *since* (tz-aware),
    oldest first — the linkage a storing agent writes into its row
    (e.g. text.llm_qa_by_research.request_ids).

    Matched on service (the resolved process name — concurrent FLOWS in
    one process must therefore collect at distinct times) + the flow's
    start time + the chat-completions endpoint, so web-search POSTs and
    pre-flow rows are excluded. Call request_log.flush() first: the
    writer is asynchronous.
    """
    rows = await conn.fetch(
        """SELECT request_id FROM text.llm_requests
            WHERE service = $1 AND created_at >= $2
              AND endpoint LIKE $3 AND NOT is_failed
            ORDER BY request_id""",
        _resolve_service(), since, "%" + endpoint_suffix)
    return [r["request_id"] for r in rows]


async def collect_request_ids(
        flow_start, conn=None, *, endpoint_suffix: str = "/chat/completions",
) -> List[int]:
    """Drain the recorder and return this process's request ids since
    *flow_start* (tz-aware) — the fail-soft convenience over flush +
    fetch_recent_request_ids that storing flows call right before their
    write: any failure logs a warning and returns [] (the row stores
    without the linkage). Opens its own asyncpg connection when *conn*
    is None.
    """
    try:
        flush(15)
        own = conn is None
        if own:
            from _common.db_commons import get_db_connection_async
            conn = await get_db_connection_async()
        try:
            return await fetch_recent_request_ids(
                conn, flow_start, endpoint_suffix=endpoint_suffix)
        finally:
            if own and conn is not None:
                with contextlib.suppress(Exception):
                    await conn.close()
    except Exception as e:  # noqa: BLE001 — linkage is non-fatal
        _warn(f"request-id collection failed: {e}")
        return []


async def load_request_sources(
        request_id: int, *, conn=None) -> Optional[Dict[str, Any]]:
    """Load one request's source data BY REF IDS — the table's contract.

    Returns ``{request: <row dict>, sources: [text.news rows],
    news_group_id, qa_id}`` or None when the request id is unknown.
    The news rows carry the full stored article (title / source / date /
    content) — no payload re-parsing. Pass an asyncpg *conn* from the
    caller's pool, or let the call open (and close) its own.
    """
    from _common.db_commons import get_db_connection_async

    own = conn is None
    if own:
        conn = await get_db_connection_async()
    try:
        head = await conn.fetchrow(
            """SELECT request_id, service, provider, model, endpoint,
                      status_code, is_failed, failure_reason, duration_ms,
                      context_ref_ids, news_group_id, qa_id, created_at
                 FROM text.llm_requests WHERE request_id = $1""", request_id)
        if head is None:
            return None
        ref_ids: Sequence[int] = head["context_ref_ids"] or []
        sources = await conn.fetch(
            """SELECT news_id, title, source, date, industry_id, content
                 FROM text.news
                WHERE news_id = ANY($1::bigint[])
                ORDER BY news_id""", list(ref_ids))
        return {
            "request": dict(head),
            "sources": [dict(r) for r in sources],
            "news_group_id": head["news_group_id"],
            "qa_id": head["qa_id"],
        }
    finally:
        if own and conn is not None:
            with contextlib.suppress(Exception):
                await conn.close()
