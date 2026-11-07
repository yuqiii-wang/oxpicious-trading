"""llm_agents.deep_agents.langgraph.state_store.writer — ``NodeRunWriter`` — the ONE-NODE-ONE-CONTEXT guard:
the context id mints at node entry, the node item stamps at
begin/finish, the context registry item closes at finish.
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
from llm_agents.deep_agents.langgraph.state_store.runtime import (  # noqa: E402
    new_context_id,
)


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
            await self._store.put_node_item(
                self._thread_id, self._node,
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
            await self._store.put_node_item(
                self._thread_id, self._node, status=status,
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
            events.publish(events.DeepAgentEvent(
                type=events.EventType.NODE_FINISHED,
                thread_id=self._thread_id,
                node=self._node, status=status,
                state_id=self._state_id,
            ))
        if self.context_id is None:
            return
        try:
            await self._store.put_context_item(
                self._thread_id, self.context_id,
                kind="node", node=self._node,
                checkpoint_id=self._checkpoint_id, model=self._model,
                n_llm_calls=n_llm_calls, n_tool_calls=n_tool_calls)
        except Exception as exc:  # noqa: BLE001 — fail-soft by contract
            logger.warning("context item write failed for node %s: %s",
                           self._node, exc)

