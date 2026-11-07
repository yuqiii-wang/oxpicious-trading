"""llm_agents.deep_agents.langgraph.state_store.store — ``DeepAgentStore`` — the BaseStore over
``llm.deep_agent_store_items``: one short-lived asyncpg
connection per batch, scalar-equality search filters, plus the
runner's fail-soft writer methods (``put_node_item`` /
``put_context_item`` / ``record_subagent_context`` /
``settle_node_items``).
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

logger = logging.getLogger("deep_agents.langgraph.state_store")
from llm_agents.deep_agents.langgraph.state_store.items import (  # noqa: E402
    NODE_STATUSES, ContextKind, ContextItemValue, NodeItemValue,
    _closed_value,
)
from llm_agents.deep_agents.langgraph.state_store.runtime import (  # noqa: E402
    _node_key, contexts_namespace, thread_namespace,
)


class DeepAgentStore(BaseStore):
    """BaseStore over ``llm.deep_agent_store_items`` — the deep-agent
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

    async def put_node_item(
        self, thread_id: str, node: str, *,
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
        value = _closed_value(
            NodeItemValue,
            {
                "kind": "node", "node": node, "status": status,
                "llm_context_id": llm_context_id,
                "checkpoint_id": checkpoint_id, "model": model,
                "error": error, "n_llm_calls": n_llm_calls,
                "n_tool_calls": n_tool_calls, "n_states": n_states,
                "answer_chars": answer_chars, "state_id": state_id,
            }, "node")
        await self.aput(thread_namespace(thread_id), _node_key(node),
                        value.model_dump())

    async def put_context_item(
            self, thread_id: str, context_id: str, *,
        kind: ContextKind, node: str, state_id: Optional[int] = None,
        parent_context_id: Optional[str] = None,
        checkpoint_id: Optional[str] = None, model: Optional[str] = None,
        n_llm_calls: int = 0, n_tool_calls: int = 0,
    ) -> None:
        """Write one LLM-context registry item — REFERENCES and counts only
        (which node, which langgraph state id, how many calls). The context's
        DATA — the messages — is never written anywhere by design."""
        value = _closed_value(
            ContextItemValue,
            {
                "kind": kind, "node": node, "state_id": state_id,
                "parent_context_id": parent_context_id,
                "checkpoint_id": checkpoint_id, "model": model,
                "n_llm_calls": n_llm_calls, "n_tool_calls": n_tool_calls,
            }, "context")
        await self.aput(contexts_namespace(thread_id), context_id,
                        value.model_dump())

    async def record_subagent_context(
            self, thread_id: str, context_id: str, *,
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
        await self.put_context_item(
            thread_id, context_id, kind="subagent",
            node=node, state_id=state_id,
            parent_context_id=parent_context_id,
            checkpoint_id=checkpoint_id, model=model,
            n_llm_calls=n_llm_calls, n_tool_calls=n_tool_calls)

    async def settle_node_items(
        self, thread_id: str, *,
        failure: str = "run ended inside this node",
    ) -> int:
        """Close node items still 'running' as failed — the cancelled-run
        path: a run unwound by POST /stop dies before the finalize node (the
        only place execute's lane normally closes), so without this the
        thread's node lane stays 'running' forever and the UI's thread view
        keeps showing the live chip. Each item is re-put with its own
        counts/context/checkpoint fields preserved, only the lifecycle
        flipped. Returns how many lanes were settled. PLAIN accessor — the
        runner calls it fail-soft through the module-level door
        (``runtime.settle_node_items(store, ...)`` — the ``store=None``
        no-op lives there)."""
        items = await self.asearch(
            thread_namespace(thread_id), filter={"kind": "node"}, limit=10)
        settled = 0
        for item in items:
            value = dict(item.value)
            if value.get("status") != "running":
                continue
            value["status"] = "failed"
            value["error"] = str(failure)[:500]
            await self.aput(thread_namespace(thread_id), item.key, value)
            settled += 1
            # The node lane's own finish notification (every node finish
            # announces itself — the WS channel contract; the stop path's
            # state-row settle event lands beside it).
            events.publish(events.DeepAgentEvent(
                type=events.EventType.NODE_FINISHED, thread_id=thread_id,
                node=str(value.get("node") or ""), status="failed",
            ))
        return settled



    async def _put(self, conn: Any, op: PutOp) -> None:
        if op.value is None:
            write = render_sql_write(
                "llm_agents/deep_agents/langgraph/state_store/"
                "deep_agent_store_items/delete_item",
                list(op.namespace), op.key)
            await conn.execute(write.sql, *write.params)
            return
        write = render_sql_write(
            "llm_agents/deep_agents/langgraph/state_store/"
            "deep_agent_store_items/upsert",
            list(op.namespace), op.key,
            json.dumps(op.value, ensure_ascii=False))
        await conn.execute(write.sql, *write.params)

    async def _get(self, conn: Any, op: GetOp) -> Optional[Item]:
        row = await conn.fetchrow(
            "SELECT key, namespace, value, created_at, updated_at "
            "FROM llm.deep_agent_store_items "
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
            f"FROM llm.deep_agent_store_items WHERE {where} "
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
            f"llm.deep_agent_store_items WHERE {where}{depth} "
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

