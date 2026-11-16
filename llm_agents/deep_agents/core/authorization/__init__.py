"""llm_agents.deep_agents.core.authorization — the deep agent's DB-table
SCOPE AUTHORIZATION module (2026-11-13). Two areas of work:

1. INIT SCOPE FROM THE UI — every super-thread run gets a THREAD
   TEMP-USER grant (the ``(super_thread_id, node_id NULL)`` row of
   ``llm.deep_agent_authorizations``) holding the request's init scope:
   the UI's ticked tables + forced schemas; runs without picks default
   to the standing schema allowlist (analysis / strategy / stats /
   text / multi_media — the operational stores llm / monitor / static /
   live are NEVER default-readable, even though the shared
   readonly_analytics role can read them).
2. NODE INHERITANCE + EXPLICIT EXTENSION — a spawned sub-node shares
   its parent's scope BY CONSTRUCTION (the :class:`AuthorizationContext`
   flows down the spawn tree; no row, no work). Only a node EXPLICITLY
   given a ``study_focus`` gets an extension row keyed
   ``(super_thread_id, node_id)``: the harness KG-locates the related
   tables (``KnowledgeGraphService.table_hits``) and unions them onto
   the parent scope — extend, never narrow. A node can never widen its
   own scope; only its parent's delegation can.

ENFORCEMENT (every agent SQL transaction falls under the scope, at the
harness seams — :mod:`.gate`): ``db_run_query`` / ``db_describe_table``
/ ``db_list_tables`` / ``text_search`` MCP calls are checked in
``core/loop.py::_execute_call`` BEFORE the shared daemon sees them, and
the stats study tools check their standing views. Out-of-scope reads
reject with a kg_search remedy (structural enforcement, not prompt
instruction — the model cannot argue past it).

KNOWN GAP (this round): the py-sandbox lane's container connects as the
shared readonly_analytics role — scripts the model writes can reach
tables the scope denies. The follow-up hardening layer: a per-run
NOLOGIN PG role (thread tables + accumulated node grants) pointed at by
the run's sandbox session, enforced by PG itself.

``DEEP_AGENT_AUTHZ=0`` disables every gate check (emergency
kill-switch; rows still record).

Layout: :mod:`.models` the typed payloads — :mod:`.store` the
persistence face — :mod:`.service` the orchestrator — :mod:`.gate` the
SQL scope gate.
"""
from __future__ import annotations

from llm_agents.deep_agents.core.authorization.gate import (
    GATED_MCP_TOOLS, ScopeViolation, SqlScopeGate, gate_enabled,
)
from llm_agents.deep_agents.core.authorization.models import (
    DEFAULT_STANDING_SCHEMAS, SOURCE_KG_EXTENSION, SOURCE_UI_INIT,
    AuthorizationContext, TablePermission,
)
from llm_agents.deep_agents.core.authorization.service import (
    AUTHORIZATION_SERVICE, AuthorizationService,
)
from llm_agents.deep_agents.core.authorization.store import (
    AUTHORIZATION_STORE, AuthorizationStore,
)

__all__ = [
    "AUTHORIZATION_SERVICE", "AUTHORIZATION_STORE",
    "AuthorizationContext", "AuthorizationService", "AuthorizationStore",
    "DEFAULT_STANDING_SCHEMAS", "GATED_MCP_TOOLS", "SOURCE_KG_EXTENSION",
    "SOURCE_UI_INIT", "ScopeViolation", "SqlScopeGate", "TablePermission",
    "gate_enabled",
]
