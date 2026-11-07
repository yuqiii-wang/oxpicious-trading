"""llm_agents.deep_agents.langgraph.state_store.runtime — the store's RUNTIME helpers: context-id minting,
namespace/key builders, the thread purge (``delete_thread_items``),
the module-level ``settle_node_items`` door (the legacy
``store=None`` no-op contract the cancel paths call), and
``entry_checkpoint_id`` (the LangGraph runtime probe).
"""
from __future__ import annotations

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel
from langgraph.config import get_config
from langgraph.store.base import (
    BaseStore, GetOp, Item, ListNamespacesOp, PutOp, SearchItem, SearchOp,
)

from _common.sql_writes import render_sql_write
from llm_agents.deep_agents import events
from llm_agents.deep_agents.supervisor import NODE_SUPERVISOR
from llm_agents.deep_agents.langgraph.state_store.items import (  # noqa: E402
    NS_CONTEXTS, NS_ROOT,
)

logger = logging.getLogger("deep_agents.langgraph.state_store")


def new_context_id() -> str:
    """One LLM context's id (uuid hex) — minted ONCE per node execution."""
    return uuid.uuid4().hex


def thread_namespace(thread_id: str) -> Tuple[str, str]:
    """The namespace of one thread's node states."""
    return (NS_ROOT, thread_id)


def contexts_namespace(thread_id: str) -> Tuple[str, str, str]:
    """The namespace of one thread's LLM-context registry."""
    return (NS_ROOT, thread_id, NS_CONTEXTS)


def _node_key(node: str) -> str:
    return f"node:{node}"








async def delete_thread_items(thread_id: str) -> None:
    """Remove one thread's store items (both namespaces) — the purge
    helper plan_store.purge_thread calls alongside its other deletes."""
    from _common.db_commons import get_db_connection_async

    conn = await get_db_connection_async()
    try:
        write = render_sql_write(
            "llm_agents/deep_agents/langgraph/state_store/"
            "deep_agent_store_items/delete_for_thread",
            [NS_ROOT, thread_id],
        )
        await conn.execute(write.sql, *write.params)
    finally:
        await conn.close()


async def settle_node_items(
    store: Optional[BaseStore], thread_id: str, *,
    failure: str = "run ended inside this node",
) -> int:
    """Module-level door over :meth:`DeepAgentStore.settle_node_items` —
    the legacy signature the cancel paths call with ``store=None``
    (a no-op: no store, nothing to settle)."""
    if store is None:
        return 0
    return await store.settle_node_items(thread_id, failure=failure)


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

