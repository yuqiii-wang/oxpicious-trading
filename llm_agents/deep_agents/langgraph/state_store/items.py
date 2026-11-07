"""llm_agents.deep_agents.langgraph.state_store.items — the CLOSED item-value schemas: the namespace/tag
constants, the whitelisted field tuples, and the typed
``NodeItemValue`` / ``ContextItemValue`` models (a message
payload key can never ride into the store).
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


NS_ROOT = "deep_agent"
NS_CONTEXTS = "contexts"

# The runner-lane nodes that write node-state items (key node:<name>).
NODE_PLAN = "plan"
NODE_EXECUTE = "execute"
# The CONTEXT-COMPACTION node (langgraph.compaction): routed in by the step
# lane when the accumulated findings approach the LLM context limit —
# its per-thread items key node:compact-<ordinal> (one per compaction,
# the ordinal of the compact state row it slotted into the plan tree).
NODE_COMPACT = "compact"
# The SUPERVISOR namespace: the SUPERVISOR WATCHDOG — an independent
# per-run agent BESIDE the graph (not a LangGraph node; its items are
# written from run_thread's tail). One item per run: 'running' from
# run start (the watching marker), closed completed-idle (no context,
# no LLM) on a healthy run, or the intervention's lifecycle when a
# root-path node failed. Its constant (NODE_SUPERVISOR) lives with the
# supervisor engine — deep_agents/supervisor.py, imported at the top.
# The HARDCODED final-result node: its fixed output is the kind
# final-result context (the run's answer document — the UI Result
# view's display source). No LLM work.
NODE_RESULT = "result"
NODE_FINALIZE = "finalize"
# A graph's FINAL OUTPUT NODE (2026-11-06 three-graph refactor): the
# fixed-directive synthesis conversation every graph closes with — the
# custom graph's seat persists the kind graph-output context (the
# chain handoff the result graph reads as its input), the result
# graph's seat the kind final-result context (the run's answer). Its
# items key node:output; no state row (the seat is graph-level, never
# a plan step).
NODE_OUTPUT = "output"

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


class NodeItemValue(BaseModel):
    """One node-state item's value — the CLOSED schema as a typed model
    (2026-11-06; the former ``_closed`` whitelist over
    ``NODE_ITEM_FIELDS``): only these keys can exist, so a message
    payload key can never ride into the store. Unknown keys are dropped
    with a warning by :func:`_closed_value` before validation."""

    kind: str = "node"
    node: str
    status: str
    llm_context_id: Optional[str] = None
    checkpoint_id: Optional[str] = None
    model: Optional[str] = None
    error: Optional[str] = None
    n_llm_calls: int = 0
    n_tool_calls: int = 0
    n_states: int = 0
    answer_chars: int = 0
    # The deep_agent_states row a STEP node's item belongs to (None for
    # the runner-lane nodes plan/finalize/result) — the UI's join key
    # between the dynamic step chain and per-node call counts.
    state_id: Optional[int] = None


class ContextItemValue(BaseModel):
    """One LLM-context registry item's value — REFERENCES and counts
    only (which node, which langgraph state id, how many calls). The
    context's DATA — the messages — is never written anywhere by
    design; the closed schema is the model's field set."""

    kind: str = "node"
    node: str
    state_id: Optional[int] = None
    parent_context_id: Optional[str] = None
    checkpoint_id: Optional[str] = None
    model: Optional[str] = None
    n_llm_calls: int = 0
    n_tool_calls: int = 0


def _closed_value(model_cls, value: Dict[str, Any], what: str):
    """The whitelist that keeps items STATES, not context data: unknown
    keys are dropped with a warning (the former ``_closed`` behavior),
    the rest validates into the typed item model."""
    known = set(model_cls.model_fields)
    extra = set(value) - known
    if extra:
        logger.warning("%s item dropped unknown fields %s (closed schema %s)",
                       what, sorted(extra), sorted(known))
    return model_cls(**{k: v for k, v in value.items() if k in known})

