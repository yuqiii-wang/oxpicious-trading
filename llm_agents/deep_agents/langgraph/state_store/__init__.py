"""llm_agents.deep_agents.langgraph.state_store — the deep-agent LangGraph store.

The OTHER LangGraph persistence primitive beside the checkpointer
(``runner.py`` compiles with AsyncPostgresSaver for per-superstep state
snapshots): a real ``langgraph.store.base.BaseStore`` — namespace/key
memory — implemented HERE over this repo's own SQL-first table
``llm.deep_agent_store_items`` (DDL ``database/sql/text/
10_deep_agent_store.sql``). The langgraph-checkpoint-postgres 3.x line
(verified through 3.2.0) ships no AsyncPostgresStore, so
:class:`DeepAgentStore` implements
the interface's single dispatch point ``abatch`` and the graph is hooked
via ``compile(store=...)`` — framework semantics, repo storage.

PERSISTENCE POLICY (the store's whole design constraint):

  * ONLY STATES are persisted — store items are node-execution states
    (lifecycle + counts) and context REFERENCES. Nothing else.
  * LLM CONTEXT DATA IS NEVER PERSISTED — no message lists, no system
    prompts, no tool-call transcripts. Every item value passes
    ``_closed_value()``, a typed-model whitelist that structurally cannot
    carry a message
    payload, and the runner's persisted ThreadState carries counts, not
    the transcript.
  * CONTEXTS ARE REFERENCED BY ID — one uuid-hex context id per LLM
    conversation; the registry item (namespace ``("deep_agent",
    thread_id, "contexts")``, key = the context id) holds the node, the
    langgraph state (checkpoint) id the context ran under and call
    counts. ``llm.deep_agent_states.llm_context_id`` points each state
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

(PACKAGE NOTE, 2026-11-07: this was the single
``langgraph/state_store.py`` module; the split moved every
class/function body byte-identical into the submodules
below, and this package re-exports the same names.)
"""
from __future__ import annotations

from llm_agents.deep_agents.supervisor import NODE_SUPERVISOR  # noqa: F401 — re-export

from .items import (
    CONTEXT_ITEM_FIELDS, NODE_COMPACT, NODE_EXECUTE, NODE_FINALIZE,
    NODE_ITEM_FIELDS, NODE_OUTPUT, NODE_PLAN, NODE_RESULT, NODE_STATUSES,
    NS_CONTEXTS, NS_ROOT, ContextKind, ContextItemValue, NodeItemValue,
    _closed_value,
)
from .runtime import (
    contexts_namespace, delete_thread_items, entry_checkpoint_id,
    new_context_id, settle_node_items, thread_namespace,
)
from .store import DeepAgentStore
from .writer import NodeRunWriter

__all__ = [
    "CONTEXT_ITEM_FIELDS", "DeepAgentStore", "NODE_COMPACT", "NODE_EXECUTE",
    "NODE_FINALIZE", "NODE_ITEM_FIELDS", "NODE_OUTPUT", "NODE_PLAN",
    "NODE_RESULT", "NODE_SUPERVISOR", "NODE_STATUSES", "NS_CONTEXTS",
    "NS_ROOT", "ContextKind",
    "NODE_SUPERVISOR", "NodeItemValue", "NodeRunWriter",
    "ContextItemValue", "_closed_value",
    "contexts_namespace", "delete_thread_items",
    "entry_checkpoint_id", "new_context_id", "settle_node_items",
    "thread_namespace",
]


