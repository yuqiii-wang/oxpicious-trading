"""llm_agents.deep_agents.graph.state_store — the deep-agent LangGraph store.

The OTHER LangGraph persistence primitive beside the checkpointer
(``runner.py`` compiles with AsyncPostgresSaver for per-superstep state
snapshots): a real ``langgraph.store.base.BaseStore`` — namespace/key
memory — implemented HERE over this repo's own SQL-first table
``text.deep_agent_store_items`` (DDL ``database/sql/text/
12_deep_agent_store.sql``). The langgraph-checkpoint-postgres 3.x line
(verified through 3.2.0) ships no AsyncPostgresStore, so
:class:`DeepAgentStore` implements
the interface's single dispatch point ``abatch`` and the graph is hooked
via ``compile(store=...)`` — framework semantics, repo storage.

PERSISTENCE POLICY (the store's whole design constraint):

  * ONLY STATES are persisted — store items are node-execution states
    (lifecycle + counts) and context REFERENCES. Nothing else.
  * LLM CONTEXT DATA IS NEVER PERSISTED — no message lists, no system
    prompts, no tool-call transcripts. Every item value passes
    ``_closed()``, a whitelist that structurally cannot carry a message
    payload, and the runner's persisted ThreadState carries counts, not
    the transcript.
  * CONTEXTS ARE REFERENCED BY ID — one uuid-hex context id per LLM
    conversation; the registry item (namespace ``("deep_agent",
    thread_id, "contexts")``, key = the context id) holds the node, the
    langgraph state (checkpoint) id the context ran under and call
    counts. ``text.deep_agent_states.llm_context_id`` points each state
    row at the context that produced it.
  * ONE NODE, ONE LLM CONTEXT — :class:`NodeRunWriter` mints exactly one
    context id at node entry and threads it through the node's engine;
    plan steps share the execute node's context, each spawned-subagent
    child mints its own (``record_subagent_context``).

Namespaces (root label must not be ``langgraph`` — BaseStore validation):

  ("deep_agent", <thread_id>)              key node:<plan|execute|result|finalize>
  ("deep_agent", <thread_id>, "contexts")  key <llm_context_id>

BaseStore details: ``aput``/``aget``/``asearch``/``adelete``/
``alist_namespaces`` all route through ``abatch``. ``supports_ttl`` is
False (the aput front door rejects TTLs before abatch). PutOp ``index``
(vector-search config) is accepted and ignored — no embeddings here.
Search ``filter`` implements scalar equality (top-level or dotted-path)
against the JSONB value; ``query`` (semantic search) is ignored. Write
volume is tiny (a handful of items per run), so every batch rides one
short-lived asyncpg connection via ``_common.db_commons`` — the same
plain-SQL rung as plan_store.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from langgraph.config import get_config
from langgraph.store.base import (
    BaseStore, GetOp, Item, ListNamespacesOp, PutOp, SearchItem, SearchOp,
)

from llm_agents.deep_agents import events

logger = logging.getLogger("deep_agents.state_store")

# Root namespace label + the store's namespaces (SQL header documents these).
NS_ROOT = "deep_agent"
NS_CONTEXTS = "contexts"

# The runner-lane nodes that write node-state items (key node:<name>).
NODE_PLAN = "plan"
NODE_EXECUTE = "execute"
# The CONTEXT-COMPACTION node (graph.compaction): routed in by the step
# lane when the accumulated findings approach the LLM context limit —
# its per-thread items key node:compact-<ordinal> (one per compaction,
# the ordinal of the compact state row it slotted into the plan tree).
NODE_COMPACT = "compact"
# The HARDCODED final-result node: its fixed output is the kind
# final-result context (the run's answer document — the UI Result
# view's display source). No LLM work.
NODE_RESULT = "result"
NODE_FINALIZE = "finalize"

NODE_ITEM_FIELDS = (
    "kind", "node", "status", "llm_context_id", "checkpoint_id", "model",
    "error", "n_llm_calls", "n_tool_calls", "n_states", "answer_chars",
    # The deep_agent_states row a STEP node's item belongs to (None for
    # the runner-lane nodes plan/finalize/result) — the UI's join key
    # between the dynamic step chain and per-node call counts.
    "state_id",
)
CONTEXT_ITEM_FIELDS = (
    "kind", "node", "state_id", "parent_context_id", "checkpoint_id",
    "model", "n_llm_calls", "n_tool_calls",
)
# Closed lifecycle vocabulary for node items (writer-controlled).
NODE_STATUSES = ("running", "completed", "failed", "skipped")

ContextKind = str  # "node" | "subagent" — open within the closed field set


def new_context_id() -> str:
    """One LLM context's id (uuid hex) — minted ONCE per node execution."""
    return uuid.uuid4().hex


def thread_namespace(thread_id: str) -> Tuple[str, str]:
    """The namespace of one thread's node states."""
    return (NS_ROOT, thread_id)


def contexts_namespace(thread_id: str) -> Tuple[str, str, str]:
    """The namespace of one thread's LLM-context registry."""
    return (NS_ROOT, thread_id, NS_CONTEXTS)


def _closed(value: Dict[str, Any], fields: Sequence[str],
            what: str) -> Dict[str, Any]:
    """The whitelist that keeps items STATES, not context data: keep the
    known keys (drop anything else with a warning). A message payload
    key can therefore never ride into the store."""
    if set(value) - set(fields):
        logger.warning("%s item dropped unknown fields %s (closed schema %s)",
                       what, sorted(set(value) - set(fields)), fields)
    return {k: v for k, v in value.items() if k in fields}


def _node_key(node: str) -> str:
    return f"node:{node}"


async def put_node_item(
    store: BaseStore, thread_id: str, node: str, *,
    status: str, llm_context_id: Optional[str],
    checkpoint_id: Optional[str], model: Optional[str],
    error: Optional[str] = None, n_llm_calls: int = 0,
    n_tool_calls: int = 0, n_states: int = 0, answer_chars: int = 0,
    state_id: Optional[int] = None,
) -> None:
    """Write one node-execution state item. PLAIN accessor — the runner
    wraps it fail-soft (a store outage must never fail a run)."""
    if status not in NODE_STATUSES:
        raise ValueError(f"node status must be one of {NODE_STATUSES}")
    value = _closed(
        {
            "kind": "node", "node": node, "status": status,
            "llm_context_id": llm_context_id,
            "checkpoint_id": checkpoint_id, "model": model,
            "error": error, "n_llm_calls": n_llm_calls,
            "n_tool_calls": n_tool_calls, "n_states": n_states,
            "answer_chars": answer_chars, "state_id": state_id,
        },
        NODE_ITEM_FIELDS, "node")
    await store.aput(thread_namespace(thread_id), _node_key(node), value)


async def put_context_item(
    store: BaseStore, thread_id: str, context_id: str, *,
    kind: ContextKind, node: str, state_id: Optional[int] = None,
    parent_context_id: Optional[str] = None,
    checkpoint_id: Optional[str] = None, model: Optional[str] = None,
    n_llm_calls: int = 0, n_tool_calls: int = 0,
) -> None:
    """Write one LLM-context registry item — REFERENCES and counts only
    (which node, which langgraph state id, how many calls). The context's
    DATA — the messages — is never written anywhere by design."""
    value = _closed(
        {
            "kind": kind, "node": node, "state_id": state_id,
            "parent_context_id": parent_context_id,
            "checkpoint_id": checkpoint_id, "model": model,
            "n_llm_calls": n_llm_calls, "n_tool_calls": n_tool_calls,
        },
        CONTEXT_ITEM_FIELDS, "context")
    await store.aput(contexts_namespace(thread_id), context_id, value)


async def record_subagent_context(
    store: BaseStore, thread_id: str, context_id: str, *,
    state_id: int, parent_context_id: Optional[str],
    checkpoint_id: Optional[str], model: Optional[str] = None,
    n_llm_calls: int = 0, n_tool_calls: int = 0,
    node: str = "spawn_subagent",
) -> None:
    """A spawned subagent's OWN context registry item (one node one
    context: the child's conversation is not the parent's). *node* is
    the child state's business slug (``fork_<topic>``), passed by the
    output hook so the registry row names the fork's topic the same
    way the states row does."""
    await put_context_item(
        store, thread_id, context_id, kind="subagent",
        node=node, state_id=state_id,
        parent_context_id=parent_context_id,
        checkpoint_id=checkpoint_id, model=model,
        n_llm_calls=n_llm_calls, n_tool_calls=n_tool_calls)


async def delete_thread_items(thread_id: str) -> None:
    """Remove one thread's store items (both namespaces) — the purge
    helper plan_store.purge_thread calls alongside its other deletes."""
    from _common.db_commons import get_db_connection_async

    conn = await get_db_connection_async()
    try:
        await conn.execute(
            "DELETE FROM text.deep_agent_store_items "
            "WHERE namespace[1:2] = $1::text[]",
            [NS_ROOT, thread_id],
        )
    finally:
        await conn.close()


async def settle_node_items(
    store: Optional[BaseStore], thread_id: str, *,
    failure: str = "run ended inside this node",
) -> int:
    """Close node items still 'running' as failed — the cancelled-run
    path: a run unwound by POST /stop dies before the finalize node (the
    only place execute's lane normally closes), so without this the
    thread's node lane stays 'running' forever and the UI's thread view
    keeps showing the live chip. Each item is re-put with its own
    counts/context/checkpoint fields preserved, only the lifecycle
    flipped. Returns how many lanes were settled. PLAIN accessor — the
    runner calls it fail-soft."""
    if store is None:
        return 0
    items = await store.asearch(
        thread_namespace(thread_id), filter={"kind": "node"}, limit=10)
    settled = 0
    for item in items:
        value = dict(item.value)
        if value.get("status") != "running":
            continue
        value["status"] = "failed"
        value["error"] = str(failure)[:500]
        await store.aput(thread_namespace(thread_id), item.key, value)
        settled += 1
        # The node lane's own finish notification (every node finish
        # announces itself — the WS channel contract; the stop path's
        # state-row settle event lands beside it).
        events.publish({
            "type": "node_finished", "thread_id": thread_id,
            "node": str(value.get("node") or ""), "status": "failed",
            "ts": events.now_iso(),
        })
    return settled


class NodeRunWriter:
    """One graph node's store writer — the one-node-one-context guard.

    Minted once per node execution (the ctor mints the context id —
    exactly one), it stamps the node item at begin/finish and the
    context registry item at finish. Every method is fail-soft: a store
    outage logs a warning and the run proceeds (the same never-block
    contract as the plan persistence — persistence may never fail the
    summary). ``llm=False`` for the finalize node (no LLM work — no
    context, no registry item)."""

    def __init__(self, store: Optional[BaseStore], thread_id: str,
                 node: str, *, model: Optional[str] = None,
                 llm: bool = True, state_id: Optional[int] = None) -> None:
        # No thread id (the plan phase degraded — the response contract
        # carries no thread): the writer self-disables, there is no home.
        self._store = store if thread_id else None
        self._thread_id = thread_id
        self._node = node
        self._model = model
        self._state_id = state_id
        # The langgraph state (checkpoint) id the node ran FROM — captured
        # at begin (the entry config's checkpoint_id) and re-stamped by
        # finish, because a put REPLACES the whole item value.
        self._checkpoint_id: Optional[str] = None
        # ONE context id per node execution — the policy's minting point.
        self.context_id: Optional[str] = new_context_id() if llm else None

    async def begin(self, checkpoint_id: Optional[str]) -> None:
        self._checkpoint_id = checkpoint_id
        if self._store is None:
            return
        try:
            await put_node_item(
                self._store, self._thread_id, self._node,
                status="running", llm_context_id=self.context_id,
                checkpoint_id=checkpoint_id, model=self._model,
                state_id=self._state_id)
        except Exception as exc:  # noqa: BLE001 — fail-soft by contract
            logger.warning("store begin failed for node %s: %s",
                           self._node, exc)

    async def finish(
        self, status: str, *, error: Optional[str] = None,
        n_llm_calls: int = 0, n_tool_calls: int = 0, n_states: int = 0,
        answer_chars: int = 0,
    ) -> None:
        """Settle the node item; for an LLM node also write its context
        registry item (the counts close the context's record — the data
        itself never existed outside the run)."""
        if self._store is None:
            return
        item_settled = False
        try:
            await put_node_item(
                self._store, self._thread_id, self._node, status=status,
                llm_context_id=self.context_id,
                checkpoint_id=self._checkpoint_id, model=self._model,
                error=error, n_llm_calls=n_llm_calls,
                n_tool_calls=n_tool_calls, n_states=n_states,
                answer_chars=answer_chars, state_id=self._state_id)
            item_settled = True
        except Exception as exc:  # noqa: BLE001 — fail-soft by contract
            logger.warning("store finish failed for node %s: %s",
                           self._node, exc)
        # EVERY node finish announces itself: the node lane is what the
        # UI's canvas circles read, and the plan/step nodes' state-row
        # events are only the indirect cover — the degraded execute node
        # and a payload-less result node settle here alone. Published
        # only after a successful put (the DB stays the source of truth
        # the UI re-reads; a failed write changed nothing).
        if item_settled:
            events.publish({
                "type": "node_finished", "thread_id": self._thread_id,
                "node": self._node, "status": status,
                **({"state_id": self._state_id}
                   if self._state_id is not None else {}),
                "ts": events.now_iso(),
            })
        if self.context_id is None:
            return
        try:
            await put_context_item(
                self._store, self._thread_id, self.context_id,
                kind="node", node=self._node,
                checkpoint_id=self._checkpoint_id, model=self._model,
                n_llm_calls=n_llm_calls, n_tool_calls=n_tool_calls)
        except Exception as exc:  # noqa: BLE001 — fail-soft by contract
            logger.warning("context item write failed for node %s: %s",
                           self._node, exc)


class DeepAgentStore(BaseStore):
    """BaseStore over ``text.deep_agent_store_items`` — the deep-agent
    LangGraph store. Holds no connection: every abatch opens one
    short-lived asyncpg connection (plain-SQL rung; write volume is a
    handful of items per run). All BaseStore front doors (aput/aget/
    asearch/adelete/alist_namespaces) route through :meth:`abatch`."""

    supports_ttl = False

    def batch(self, ops: Iterable[Any]) -> List[Any]:  # type: ignore[override]
        """The sync front door — NOT implemented: the deep-agent store
        is async-only (asyncpg over the repo's connection helpers; the
        runner and server never drive the graph synchronously)."""
        raise NotImplementedError(
            "DeepAgentStore is async-only — use abatch/aput/aget/asearch")

    async def abatch(self, ops: Iterable[Any]) -> List[Any]:
        """Execute a batch of PutOp/GetOp/SearchOp/ListNamespacesOp in
        order, one connection for the whole batch."""
        from _common.db_commons import get_db_connection_async

        results: List[Any] = []
        conn = await get_db_connection_async()
        try:
            for op in ops:
                if isinstance(op, PutOp):
                    results.append(await self._put(conn, op))
                elif isinstance(op, GetOp):
                    results.append(await self._get(conn, op))
                elif isinstance(op, SearchOp):
                    results.append(await self._search(conn, op))
                elif isinstance(op, ListNamespacesOp):
                    results.append(await self._list_namespaces(conn, op))
                else:
                    raise NotImplementedError(f"unsupported store op {op!r}")
        finally:
            await conn.close()
        return results

    # -- the four ops ---------------------------------------------------

    async def _put(self, conn: Any, op: PutOp) -> None:
        if op.value is None:
            await conn.execute(
                "DELETE FROM text.deep_agent_store_items "
                "WHERE namespace = $1::text[] AND key = $2",
                list(op.namespace), op.key)
            return
        await conn.execute(
            "INSERT INTO text.deep_agent_store_items (namespace, key, value) "
            "VALUES ($1::text[], $2, $3::jsonb) "
            "ON CONFLICT (namespace, key) DO UPDATE SET "
            "value = EXCLUDED.value, updated_at = now()",
            list(op.namespace), op.key,
            json.dumps(op.value, ensure_ascii=False))

    async def _get(self, conn: Any, op: GetOp) -> Optional[Item]:
        row = await conn.fetchrow(
            "SELECT key, namespace, value, created_at, updated_at "
            "FROM text.deep_agent_store_items "
            "WHERE namespace = $1::text[] AND key = $2",
            list(op.namespace), op.key)
        return _row_item(row) if row is not None else None

    async def _search(self, conn: Any, op: SearchOp) -> List[SearchItem]:
        where, args = "namespace[1:$1] = $2::text[]", \
            [len(op.namespace_prefix), list(op.namespace_prefix)]
        # Scalar equality on the value (top-level or dotted path):
        # {"a": 1} or {"a.b": 1} → value #> ARRAY['a','b'] = 1::jsonb.
        for key, want in (op.filter or {}).items():
            path = key.split(".")
            args.extend([path, json.dumps(want, ensure_ascii=False)])
            where += f" AND value #> ${len(args) - 1}::text[] " \
                     f"= ${len(args)}::jsonb"
        # Freshness first (the UI's live poll reads the newest state).
        rows = await conn.fetch(
            f"SELECT key, namespace, value, created_at, updated_at "
            f"FROM text.deep_agent_store_items WHERE {where} "
            f"ORDER BY updated_at DESC, item_id DESC "
            f"LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
            *args, op.limit, op.offset)
        return [_row_search_item(r) for r in rows]

    async def _list_namespaces(
        self, conn: Any, op: ListNamespacesOp,
    ) -> List[Tuple[str, ...]]:
        # Include/exclude prefix matching (BaseStore's prefix/suffix
        # conditions boil down to path matching); max_depth caps each
        # namespace at its first N components.
        where, args = "TRUE", []  # type: List[Any]
        for cond in op.match_conditions:
            args.extend([len(cond.path), list(cond.path)])
            clause = (f"namespace[1:${len(args) - 1}] "
                      f"= ${len(args)}::text[]")
            where += (f" AND NOT ({clause})" if cond.match_type == "exclude"
                      else f" AND ({clause})")
        depth = ""
        if op.max_depth is not None:
            args.append(op.max_depth)
            depth = f" AND cardinality(namespace) <= ${len(args)}"
        rows = await conn.fetch(
            f"SELECT DISTINCT namespace FROM "
            f"text.deep_agent_store_items WHERE {where}{depth} "
            f"ORDER BY namespace LIMIT ${len(args) + 1} "
            f"OFFSET ${len(args) + 2}",
            *args, op.limit, op.offset)
        return [tuple(r["namespace"]) for r in rows]


def _jsonb_dict(value: Any) -> Dict[str, Any]:
    """asyncpg hands JSONB back as the raw JSON string unless a codec
    was registered — Item.value must be the parsed dict either way."""
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    return dict(value)


def _row_item(row: Any) -> Item:
    return Item(
        namespace=tuple(row["namespace"]), key=row["key"],
        value=_jsonb_dict(row["value"]), created_at=row["created_at"],
        updated_at=row["updated_at"])


def _row_search_item(row: Any) -> SearchItem:
    return SearchItem(
        namespace=tuple(row["namespace"]), key=row["key"],
        value=_jsonb_dict(row["value"]), created_at=row["created_at"],
        updated_at=row["updated_at"])

def entry_checkpoint_id() -> Optional[str]:
    """The langgraph state id of THIS node's superstep — the checkpoint
    id the runtime mints for the node's execution (each step hop is its
    own superstep). Verified on langgraph 1.2.0 and 1.2.18; resolved in
    order of increasing privateness:
    ``__pregel_runtime.execution_info.checkpoint_id`` (present in nodes
    with or without a checkpointer), the ``checkpoint_map`` configurable
    (superstep -> checkpoint id, no checkpointer needed), then the plain
    ``configurable.checkpoint_id`` (set on resume). None outside a graph
    run (direct node unit tests)."""
    try:
        config = get_config()
    except Exception:  # noqa: BLE001 — no run context, no checkpoint
        return None
    configurable = config.get("configurable") or {}
    runtime = configurable.get("__pregel_runtime")
    info = getattr(runtime, "execution_info", None)
    if info is not None and info.checkpoint_id:
        return str(info.checkpoint_id)
    checkpoint_map = configurable.get("checkpoint_map")
    if isinstance(checkpoint_map, dict) and checkpoint_map:
        checkpoint_id = next(reversed(checkpoint_map.values()))
        if checkpoint_id:
            return str(checkpoint_id)
    checkpoint_id = configurable.get("checkpoint_id")
    return str(checkpoint_id) if checkpoint_id else None
