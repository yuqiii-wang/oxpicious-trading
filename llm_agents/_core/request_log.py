"""llm_agents._core.request_log — source LLM request/response recorder.

Appends one row per logical LLM API call to ``text.llm_requests``
(database/sql/text/07_llm_requests.sql): the full request payload and
the FINAL attempt's response payload as JSONB, the coarse outcome
(status / is_failed / failure_reason / duration_ms — the same grain as
``monitor.external_resource_audit``), the call's usage token counts
(input / output / total — extracted from the response's usage block,
see :func:`_extract_usage`), and the request's context /
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

Chain linkage: a multi-request FLOW wraps its generate call in
``bind_request_chain()`` — every request inside gets the same
``chain_id``, the writer links each row to its chain predecessor via
``prev_request_id``, and from the second request on the stored
``request_payload`` is INCREMENTAL (top-level keys / messages already
sent by the chain's previous request are removed — the shared system
prompt / context blocks are stored once, in the chain's first row).
``reconstruct_request_payload`` merges a row's chain back into the
full request as sent. One-off requests (no chain binding) store the
full payload as before.

Search index (``search_tsv``, 07_llm_requests.sql's migration): every
recorded row also carries a weighted GIN-indexed tsvector over its
conversation DELTA — A = the incremental input (``request_payload.
messages`` as stored), B = the output (the response's assistant text +
tool-call names/arguments) — Chinese pre-segmented by the repo's
StructBERT segmenter (``_common.tokenization``, the same contract as
the text schema's news/Q&A tables). Tokenization runs in the WRITER
thread (never on the request path); a tokenizer failure stores the row
with a NULL vector — :func:`backfill_search_tsv_async` (optionally
scoped to a set of chains) heals NULL rows, and the deep agents'
thread-internal search tool heals its own thread's rows before
searching.

Never-blocks contract: identical in spirit to
``_common/external_resource_audit.py`` (bounded queue, one lazy daemon
writer thread, batched executemany over a persistent autocommit
connection, DB outages drop rows instead of perturbing requests). The
service name resolves through the audit module's resolver (imported, not
duplicated). db_commons is imported LAZILY inside the writer thread (the
cudf.pandas activation-order rule).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import queue
import threading
import time
from contextvars import ContextVar
from typing import (Any, Dict, Iterable, List, Optional, Sequence,
                    Tuple)

# Payload hygiene caps: a base64 screenshot is not source data worth
# storing; failure reasons stay short like the audit's.
_DATA_URL_PREFIX = "data:image/"
_DATA_URL_PLACEHOLDER = "[screenshot omitted]"
_REASON_MAX = 500
_QUEUE_MAX = 2000
_BATCH_MAX = 100
_WARN_EVERY_S = 1800.0

# search_tsv extraction caps (the vector indexes the IMPORTANT data, not
# the blob — module docstring's Search index section): per-message input
# text, the whole request's input total, the response content, and each
# tool call's argument fragment; at most this many tool calls per row.
_SEARCH_MSG_MAX = 2000
_SEARCH_INPUT_MAX = 24000
_SEARCH_OUTPUT_MAX = 2000
_SEARCH_TOOL_ARG_MAX = 400
_SEARCH_TOOL_CALLS_MAX = 20

# (news_ids tuple, news_group_id, qa_id) bound to the current context.
_refs: ContextVar[Optional[tuple]] = ContextVar(
    "llm_request_refs", default=None)

# Chain id of the multi-request flow bound to the current context (see
# bind_request_chain).
_chain: ContextVar[Optional[str]] = ContextVar(
    "llm_request_chain", default=None)

# Last FULL (sanitized) request payload per active chain — the dedupe
# base for the next chained request's incremental payload. Keyed by
# chain id, then by payload shape ("messages" in payload or not): a chat
# completion diffs against the chain's previous chat completion and a
# web-search POST against the previous search POST (cross-shape payloads
# share nothing meaningful). Flows are sequential within a chain, so
# last-write-wins is the predecessor. Best-effort only (a lost entry
# just stores the next payload in full).
_chain_last_payload: Dict[str, Dict[bool, Dict[str, Any]]] = {}

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


@contextlib.contextmanager
def bind_request_chain(chain_id: Optional[str] = None):
    """Context manager binding a multi-request FLOW into one chain.

    Every request recorded inside the block gets the same ``chain_id``
    in text.llm_requests, and — from the second request on — an
    INCREMENTAL ``request_payload``: top-level keys and messages already
    present in the chain's previous request are removed (the shared
    system prompt / context blocks are stored once, in the chain's
    first row). The chain's predecessor is linkable via
    ``prev_request_id`` (backfilled by the writer after insert), so the
    full request as sent = merge of the chain up to that row
    (:func:`reconstruct_request_payload`). Nesting reuses the outer
    chain unless an explicit *chain_id* is given.
    """
    outer = _chain.get()
    if chain_id is None and outer is not None:
        token = _chain.set(outer)  # keep outer on reset
    else:
        import uuid
        token = _chain.set(chain_id or uuid.uuid4().hex)
    try:
        yield
    finally:
        _chain.reset(token)


def current_chain_id() -> Optional[str]:
    """The context's bound chain id (None outside a chain)."""
    return _chain.get()


def end_request_chain(chain_id: Optional[str]) -> None:
    """Drop a finished chain's last-payload cache entry.

    A chain's last FULL payload stays in ``_chain_last_payload`` as the
    dedupe base for the chain's next request; once the conversation is
    over (its last request already recorded) the entry is dead weight —
    one full payload per chain for the process's whole life. Fine for a
    short CLI flow, unbounded for a long-lived server that mints one
    chain per run: the deep-agent runner calls this when a node's
    context chain closes. Best effort — a chain that records again
    (should not happen) simply re-seeds its entry and stores in full.
    """
    if chain_id:
        _chain_last_payload.pop(chain_id, None)


def _diff_payload(full: Dict[str, Any], prev: Dict[str, Any]) -> Dict[str, Any]:
    """The chain-incremental view of *full* against the previous
    request's FULL payload: top-level keys equal to the predecessor's are
    dropped; within ``messages``, entries byte-identical to a predecessor
    message (the shared system prompt / context blocks) are removed and
    their count noted. The ``model`` key always rides along (the
    replay-critical minimum) and the result is tagged
    ``_chain_incremental``."""
    incremental: Dict[str, Any] = {}
    for key, value in full.items():
        if (key == "messages" and isinstance(value, list)
                and isinstance(prev.get(key), list)):
            shared = {json.dumps(m, ensure_ascii=False, sort_keys=True)
                      for m in prev[key]}
            kept = [m for m in value
                    if json.dumps(m, ensure_ascii=False, sort_keys=True)
                    not in shared]
            if len(kept) != len(value):
                incremental["_chain_shared_messages"] = len(value) - len(kept)
                incremental["messages"] = kept
                continue
        if prev.get(key) != value:
            incremental[key] = value
    # Replay-critical minimum: the model rides along even when unchanged.
    if "model" in full and "model" not in incremental:
        incremental["model"] = full["model"]
    incremental["_chain_incremental"] = True
    return incremental


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


def _extract_message_text(message: Any) -> str:
    """One chat message's TEXT content (content parts only — image_url
    placeholders are already short strings), capped per message."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content[:_SEARCH_MSG_MAX]
    if isinstance(content, list):
        parts = [p.get("text") for p in content
                 if isinstance(p, dict) and isinstance(p.get("text"), str)]
        return " ".join(parts)[:_SEARCH_MSG_MAX]
    return ""


def _extract_request_texts(
        request_payload: Optional[Dict[str, Any]],
        response_payload: Optional[Dict[str, Any]],
) -> Tuple[str, str]:
    """The row's ``(input_text, output_text)`` for search_tsv — the
    request's conversation delta, bounded.

    Input: ``request_payload.messages`` AS STORED — a chained row's list
    is already the incremental view (shared messages removed); a chain's
    first row and one-offs carry the full list (the conversation base).
    Output: the final response's assistant text plus its tool calls'
    function names and (capped) arguments — the model's answer and the
    calls it made. NO reasoning traces: thinking/reasoning_content is
    internal and never indexed (the context-hygiene contract)."""
    input_text = ""
    if isinstance(request_payload, dict):
        messages = request_payload.get("messages")
        if isinstance(messages, list):
            fragments = [t for t in (_extract_message_text(m)
                                     for m in messages) if t]
            input_text = " ".join(fragments)[:_SEARCH_INPUT_MAX]
    output_parts: List[str] = []
    if isinstance(response_payload, dict):
        choices = response_payload.get("choices")
        message = (choices[0].get("message") if isinstance(choices, list)
                   and choices and isinstance(choices[0], dict) else None)
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content:
                output_parts.append(content[:_SEARCH_OUTPUT_MAX])
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for call in tool_calls[:_SEARCH_TOOL_CALLS_MAX]:
                    function = (call.get("function")
                                if isinstance(call, dict) else None)
                    if not isinstance(function, dict):
                        continue
                    name = function.get("name") or ""
                    arguments = function.get("arguments") or ""
                    output_parts.append(
                        f"{name} {str(arguments)[:_SEARCH_TOOL_ARG_MAX]}")
    return input_text, " ".join(p for p in output_parts if p)


def _tokenize_search_pairs(pairs: Sequence[Tuple[str, str]]
                           ) -> List[Tuple[Optional[str], Optional[str]]]:
    """Tokenize a batch's (input, output) text pairs for the tsv INSERT —
    ONE segmenter pass for the whole batch, run in the WRITER thread. A
    tokenizer failure (model unavailable, DB skip-word load hiccup)
    returns all-None pairs: those rows store a NULL vector (the backfill
    heals them), never a dropped row."""
    flat = [text for pair in pairs for text in pair]
    try:
        from _common.tokenization import SearchIndexer

        tokens = SearchIndexer.get().tokenize_for_search_batch(flat)
    except Exception as exc:  # noqa: BLE001 — never drop rows over tsv
        _warn(f"search_tsv tokenization failed ({exc!r}) — rows stored "
              f"unindexed (backfill heals)")
        return [(None, None)] * len(pairs)
    # '' stays '' (to_tsvector of '' is a valid EMPTY vector — the row is
    # processed, the backfill never re-visits it); None marks the
    # tokenizer-failure path (NULL vector, healed later).
    return [(tokens[i * 2], tokens[i * 2 + 1]) for i in range(len(pairs))]


def _extract_usage(data: Optional[Dict[str, Any]]
                   ) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """The response's ``(input_tokens, output_tokens, total_tokens)``.

    Read from the OpenAI-style ``usage`` block every chat provider on
    this transport returns (ZhiPu: prompt_tokens / completion_tokens /
    total_tokens); all-None when the body carries no usage block
    (web-search POSTs, failures, unexpected shapes).
    """
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return (None, None, None)

    def _count(key: str) -> Optional[int]:
        value = usage.get(key)
        # bool is an int subclass — usage counts are plain ints only.
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return (_count("prompt_tokens"), _count("completion_tokens"),
            _count("total_tokens"))


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
    (None on failure) — its usage block yields the row's
    input/output/total token counts. The ref-id binding of the calling
    context rides along.
    """
    news_ids, group_id, qa_id = current_refs()
    chain_id = _chain.get()
    input_tokens, output_tokens, total_tokens = _extract_usage(data)
    full = _sanitize(payload)
    stored_payload: Dict[str, Any] = full
    if chain_id:
        shape = "messages" in full
        prev = _chain_last_payload.get(chain_id, {}).get(shape)
        if prev is not None:
            try:
                stored_payload = _diff_payload(full, prev)
            except Exception:  # noqa: BLE001 — the full payload is the fallback
                stored_payload = full
        _chain_last_payload.setdefault(chain_id, {})[shape] = full
    sanitized_response = _sanitize(data)
    search_in, search_out = _extract_request_texts(
        stored_payload, sanitized_response)
    row = (
        _resolve_service(), provider,
        model or (payload.get("model") if isinstance(payload, dict) else None),
        _lean_url(url),
        json.dumps(stored_payload, ensure_ascii=False),
        json.dumps(sanitized_response, ensure_ascii=False) if data is not None else None,
        status_code, is_failed,
        (failure_reason[:_REASON_MAX] if failure_reason else None),
        (round(duration_ms) if duration_ms is not None else None),
        input_tokens, output_tokens, total_tokens,
        list(news_ids), group_id, qa_id, chain_id,
        # The row's search_tsv source texts (raw — tokenization happens
        # in the WRITER thread, off the request path).
        search_in, search_out,
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
     duration_ms, input_tokens, output_tokens, total_tokens,
     context_ref_ids, news_group_id, qa_id, chain_id, search_tsv)
VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        strip(setweight(to_tsvector('simple', %s::text), 'A') ||
              setweight(to_tsvector('simple', %s::text), 'B')))
"""

# After each insert batch: link every fresh chained row to its chain
# predecessor (previous request_id of the same chain_id). Rows already
# linked (earlier batches) are skipped via prev_request_id IS NULL; the
# correlated subquery resolves sequentially inside one statement, so a
# batch of N chained rows links N-1+1 predecessors correctly.
_BACKFILL_PREV_SQL = """
UPDATE text.llm_requests r
   SET prev_request_id = (
         SELECT q.request_id FROM text.llm_requests q
          WHERE q.chain_id = r.chain_id AND q.request_id < r.request_id
          ORDER BY q.request_id DESC LIMIT 1)
 WHERE r.chain_id = %s AND r.prev_request_id IS NULL
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
                # search_tsv: tokenize the batch's (input, output) texts
                # (writer thread — the model loads lazily on the first
                # CJK batch; a failure stores NULL vectors, not lost
                # rows), then hand the token strings to executemany.
                pairs = _tokenize_search_pairs(
                    [(row[-2], row[-1]) for row in batch])
                rows_tsv = [row[:-2] + (in_tok, out_tok)
                            for row, (in_tok, out_tok)
                            in zip(batch, pairs)]
                cur.executemany(_INSERT_SQL, rows_tsv)
                chained = {row[-3] for row in rows_tsv if row[-3]}
                for cid in chained:
                    cur.execute(_BACKFILL_PREV_SQL, (cid,))
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


async def backfill_search_tsv_async(
        conn=None, *, chains: Optional[Sequence[str]] = None,
        chunk_rows: int = 500,
) -> int:
    """Heal rows whose search_tsv IS NULL — tokenize their stored payloads
    and set the vector, chunked with the repo's commit + cool-down
    discipline. *chains* scopes the heal to one set of request chains
    (the deep agents' thread-internal search passes its thread's chain
    ids so a search first indexes whatever the writer stored unindexed);
    None heals the whole table. Resumable: the NULL filter is the work
    queue. Returns the number of rows indexed. Any failure raises to the
    caller (the tool path renders it as tool text)."""
    from _common.db_commons import get_db_connection_async

    own = conn is None
    if own:
        conn = await get_db_connection_async()
    total = 0
    try:
        while True:
            rows = await conn.fetch(
                """SELECT request_id, request_payload, response_payload
                     FROM text.llm_requests
                    WHERE search_tsv IS NULL"""
                + (" AND chain_id = ANY($2::text[])" if chains else "")
                + " LIMIT $1",
                *([chunk_rows, list(chains)] if chains else [chunk_rows]))
            if not rows:
                break
            pairs: List[Tuple[str, str]] = []
            for row in rows:
                req = row["request_payload"]
                resp = row["response_payload"]
                # asyncpg hands JSONB back as the raw JSON string.
                req = json.loads(req) if isinstance(req, (str, bytes,
                                                          bytearray)) else req
                resp = (json.loads(resp) if isinstance(
                    resp, (str, bytes, bytearray)) else resp)
                pairs.append(_extract_request_texts(req, resp))
            tokens = _tokenize_search_pairs(pairs)
            await conn.executemany(
                """UPDATE text.llm_requests
                      SET search_tsv = strip(
                            setweight(to_tsvector('simple', $2::text), 'A')
                            || setweight(to_tsvector('simple', $3::text),
                                         'B'))
                    WHERE request_id = $1""",
                [(row["request_id"], in_tok, out_tok)
                 for row, (in_tok, out_tok) in zip(rows, tokens)])
            total += len(rows)
            await asyncio.sleep(0.1)
    finally:
        if own and conn is not None:
            with contextlib.suppress(Exception):
                await conn.close()
    return total


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
                      input_tokens, output_tokens, total_tokens,
                      context_ref_ids, news_group_id, qa_id, chain_id,
                      prev_request_id, created_at
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


async def reconstruct_request_payload(
        request_id: int, *, conn=None) -> Optional[Dict[str, Any]]:
    """The FULL request payload of a (possibly chain-incremental) row.

    Walks ``prev_request_id`` back to the chain root and merges forward:
    the root row's payload is the base, and each later row's incremental
    payload contributes its own top-level keys plus any messages not
    already accumulated (the mirror of :func:`_diff_payload`). Returns
    None for an unknown request id; a broken chain (missing predecessor)
    yields the best-effort merge of what is stored. Pass an asyncpg
    *conn* from the caller's pool, or let the call open (and close) its
    own.
    """
    from _common.db_commons import get_db_connection_async

    own = conn is None
    if own:
        conn = await get_db_connection_async()
    try:
        chain: List[Dict[str, Any]] = []
        rid = request_id  # type: int | None
        while rid is not None:
            row = await conn.fetchrow(
                """SELECT request_id, prev_request_id, request_payload
                     FROM text.llm_requests WHERE request_id = $1""", rid)
            if row is None:
                break
            chain.append(dict(row))
            rid = row["prev_request_id"]
        if not chain:
            return None
        merged: Dict[str, Any] = {}
        seen_msgs: set = set()
        for row in reversed(chain):  # root first
            payload = row["request_payload"]
            if isinstance(payload, str):  # asyncpg returns jsonb as str
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                continue
            for key, value in payload.items():
                if key in ("_chain_incremental", "_chain_shared_messages"):
                    continue
                if key == "messages" and isinstance(value, list):
                    base = merged.setdefault("messages", [])
                    for m in value:
                        sig = json.dumps(m, ensure_ascii=False, sort_keys=True)
                        if sig not in seen_msgs:
                            seen_msgs.add(sig)
                            base.append(m)
                    continue
                merged[key] = value
        return merged
    finally:
        if own and conn is not None:
            with contextlib.suppress(Exception):
                await conn.close()
