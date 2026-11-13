"""llm_agents._core.aux_steps — the deep-agent AUXILIARY step recorder.

Appends one row per auxiliary harness op to ``llm.deep_agent_aux_steps``
(database/sql/llm/deep_agent_aux_steps.sql): the System One (jev)
DECISION reads (typed facts in, calibrated verdict out — the injected
``JevDecisionEngine`` funnels every ``decide_*`` through its ``_read``,
the one recording seam) and the StructBERT TOKENIZATION operations (a
request-log row's ``search_tsv`` vector, a context row's ``tags``
vector, a search query's pre-segmentation). The table's rows are for
OBSERVABILITY only — the UI's Thread Graph "Agent steps" list joins
them onto a task state by ``llm_context_id`` (the ambient request
chain, the same join the inference/request rows use) — and their
payloads are NEVER loaded into any LLM conversation.

Never-blocks contract: identical in spirit to ``request_log`` (bounded
queue, one lazy daemon writer thread, batched executemany over a
persistent connection, DB outages drop rows instead of perturbing the
callers). db_commons / sql_writes / labels import LAZILY inside the
recording paths (the cudf.pandas activation-order rule). Every enqueue
is fail-soft: an aux step must never break the work it observed.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from typing import Any, Dict, Optional, Sequence, Tuple

from pydantic import BaseModel

logger = logging.getLogger("llm_aux_steps")

_QUEUE_MAX = 2000
_BATCH_MAX = 100
_WARN_EVERY_S = 1800.0
# JSONB payload caps — a decision's facts or a context's text can be
# large; the step row is a REFERENCE, not the artifact (house rule:
# artifacts stay in files, references in context — and the UI's raw
# view only needs the shape).
_PAYLOAD_MAX_CHARS = 2048
_TEXT_MAX_CHARS = 500

# The agent_step_type codes this table stores (vocabulary of record:
# static.label_dim 'agent_step_type', mirrored in _common.labels
# AGENT_STEP_TYPE_CODE + the TS label-codes mirror).
STEP_DECISION = 2
STEP_TOKENIZATION = 3

# The tokenization lanes (llm.deep_agent_aux_steps.lane, open vocabulary).
LANE_SEARCH_TSV = "search_tsv"
LANE_CONTEXT_TAGS = "context_tags"
LANE_QUERY_SEGMENT = "query_segment"

_INSERT_SQL_NAME = ("llm_agents/_core/aux_steps/deep_agent_aux_steps"
                    "/insert")

# The action file keeps the tree's dense $n contract; this executor is
# psycopg (sync, in the writer thread), whose placeholders are %s — the
# translation is positional and safe because the contract guarantees
# dense $1..$n in ascending order of appearance.
_DOLLAR_PH_RE = re.compile(r"\$\d+")


def _psycopg_sql(action_sql: str) -> str:
    """The $n action SQL → the %s face this executor binds. Comment
    lines keep their text but get ``%`` escaped (``%%``): psycopg's
    client-side bind counts every bare ``%`` in the statement text —
    including inside ``--`` comments — and the action file's header
    legitimately documents its params as $n and mentions %s."""
    out: list[str] = []
    for line in action_sql.splitlines(keepends=True):
        if line.lstrip().startswith("--"):
            out.append(line.replace("%", "%%"))
        else:
            out.append(_DOLLAR_PH_RE.sub("%s", line))
    return "".join(out)


class AuxStepSpec(BaseModel):
    """One auxiliary step row — the typed contract crossing module
    boundaries (the lanes build this; the recorder encodes it)."""

    step_type: int
    lane: str
    title: str
    thread_id: Optional[str] = None
    super_thread_id: Optional[str] = None
    state_id: Optional[int] = None
    llm_context_id: Optional[str] = None
    input_text: Optional[str] = None
    output_text: Optional[str] = None
    input_payload: Optional[Dict[str, Any]] = None
    output_payload: Optional[Dict[str, Any]] = None
    duration_ms: Optional[int] = None
    is_failed: bool = False
    error: Optional[str] = None


def _cap_text(text: Optional[str]) -> Optional[str]:
    if text is None or len(text) <= _TEXT_MAX_CHARS:
        return text
    return text[:_TEXT_MAX_CHARS].rstrip() + " …"


def _cap_payload(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    """The JSONB body as serialized text, size-capped: an oversized
    payload stores an honest ``_truncated`` stub (head + true size),
    never a broken JSON document."""
    if payload is None:
        return None
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = json.dumps({"repr": str(payload)[:_PAYLOAD_MAX_CHARS]},
                          ensure_ascii=False)
    if len(text) <= _PAYLOAD_MAX_CHARS:
        return text
    return json.dumps({
        "_truncated": True,
        "chars": len(text),
        "head": text[:_PAYLOAD_MAX_CHARS],
    }, ensure_ascii=False)


def _encode(spec: AuxStepSpec) -> Tuple:
    """The spec → the insert's ordered parameter tuple (dense $1..$14)."""
    return (
        spec.thread_id, spec.super_thread_id, spec.state_id,
        spec.llm_context_id, spec.step_type, spec.lane, spec.title,
        _cap_text(spec.input_text), _cap_text(spec.output_text),
        _cap_payload(spec.input_payload), _cap_payload(spec.output_payload),
        spec.duration_ms, spec.is_failed,
        (_cap_text(spec.error) if spec.error else None),
    )


class AuxStepRecorder:
    """The aux-step WRITER — one process-wide instance (``get()``), one
    bounded queue, one lazy daemon writer thread, batched executemany
    over a persistent connection. ``record`` never raises and never
    blocks; a DB outage drops rows (throttled warning), never perturbs
    the op being recorded."""

    _instance: Optional["AuxStepRecorder"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._queue: "queue.Queue[Tuple]" = queue.Queue(maxsize=_QUEUE_MAX)
        self._writer_lock = threading.Lock()
        self._writer: Optional[threading.Thread] = None
        self._last_warn = 0.0

    @classmethod
    def get(cls) -> "AuxStepRecorder":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ------------------------------------------------------------ enqueue
    def record(self, spec: AuxStepSpec) -> None:
        """Enqueue one aux-step row. An unset ``llm_context_id`` fills
        from the ambient request chain (``request_log.current_chain_id``
        — a decision read or a query segmentation inside a bound
        conversation lands on that conversation's chain, which is what
        the UI's steps view joins)."""
        if spec.llm_context_id is None:
            from llm_agents._core.request_log import current_chain_id
            spec = spec.model_copy(
                update={"llm_context_id": current_chain_id()})
        self._enqueue([_encode(spec)])

    def record_many(self, specs: Sequence[AuxStepSpec]) -> None:
        """Enqueue a batch (the request-log writer's per-batch lane —
        one enqueue, not one per row)."""
        if not specs:
            return
        self._enqueue([_encode(s) for s in specs])

    def flush(self, timeout_s: float = 5.0) -> bool:
        """Block until the queue drained (tests / graceful shutdown)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and not self._queue.empty():
            time.sleep(0.05)
        if not self._queue.empty():
            return False
        time.sleep(0.3)
        return self._queue.empty()

    # ------------------------------------------------------------- writer
    def _enqueue(self, rows: Sequence[Tuple]) -> None:
        try:
            for row in rows:
                self._queue.put_nowait(row)
        except queue.Full:
            self._warn("queue full — dropping aux-step rows "
                       "(writer wedged or DB down?)")
            return
        self._ensure_writer()

    def _warn(self, msg: str) -> None:
        now = time.monotonic()
        if now - self._last_warn < _WARN_EVERY_S:
            return
        self._last_warn = now
        logger.warning("[aux_steps] %s", msg)

    def _ensure_writer(self) -> None:
        if self._writer is not None and self._writer.is_alive():
            return
        with self._writer_lock:
            if self._writer is not None and self._writer.is_alive():
                return
            self._writer = threading.Thread(
                target=self._run, name="aux-steps-writer", daemon=True)
            self._writer.start()

    def _run(self) -> None:
        conn = None
        insert_sql = None
        while True:
            try:
                row = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            rows = [row]
            while len(rows) < _BATCH_MAX:
                try:
                    rows.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            if conn is None:
                try:
                    # The lazy import chain (db_commons + sql_writes) —
                    # the cudf.pandas activation-order rule.
                    from _common.db_commons import get_db_connection
                    from _common.sql_writes import load_sql_write

                    conn = get_db_connection()
                    insert_sql = _psycopg_sql(
                        load_sql_write(_INSERT_SQL_NAME))
                except Exception as exc:  # noqa: BLE001 — never kill writer
                    self._warn(f"connect failed, dropping {len(rows)} "
                               f"row(s): {exc}")
                    continue
            try:
                with conn.cursor() as cur:
                    cur.executemany(insert_sql, rows)
                if not getattr(conn, "autocommit", False):
                    conn.commit()
            except Exception as exc:  # noqa: BLE001 — drop, reconnect
                self._warn(f"insert failed, dropping {len(rows)} "
                           f"row(s): {exc}")
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = None
                insert_sql = None


def record_decision(
    *, lane: str, title: str, output_text: str,
    input_text: Optional[str] = None,
    input_payload: Optional[Dict[str, Any]] = None,
    output_payload: Optional[Dict[str, Any]] = None,
    duration_ms: Optional[int] = None,
    is_failed: bool = False, error: Optional[str] = None,
    thread_id: Optional[str] = None, super_thread_id: Optional[str] = None,
    state_id: Optional[int] = None, llm_context_id: Optional[str] = None,
) -> None:
    """Record one System One decision read (``step_type=decision``).
    The tiny free-function face of :meth:`AuxStepRecorder.record` —
    the call sites are one-liners inside fail-soft lanes."""
    AuxStepRecorder.get().record(AuxStepSpec(
        step_type=STEP_DECISION, lane=lane, title=title,
        input_text=input_text, output_text=output_text,
        input_payload=input_payload, output_payload=output_payload,
        duration_ms=duration_ms, is_failed=is_failed, error=error,
        thread_id=thread_id, super_thread_id=super_thread_id,
        state_id=state_id, llm_context_id=llm_context_id))


def record_tokenization(
    *, lane: str, title: str, output_text: str,
    input_text: Optional[str] = None,
    input_payload: Optional[Dict[str, Any]] = None,
    output_payload: Optional[Dict[str, Any]] = None,
    duration_ms: Optional[int] = None,
    thread_id: Optional[str] = None, super_thread_id: Optional[str] = None,
    state_id: Optional[int] = None, llm_context_id: Optional[str] = None,
) -> None:
    """Record one StructBERT tokenization op
    (``step_type=tokenization``)."""
    AuxStepRecorder.get().record(AuxStepSpec(
        step_type=STEP_TOKENIZATION, lane=lane, title=title,
        input_text=input_text, output_text=output_text,
        input_payload=input_payload, output_payload=output_payload,
        duration_ms=duration_ms,
        thread_id=thread_id, super_thread_id=super_thread_id,
        state_id=state_id, llm_context_id=llm_context_id))
