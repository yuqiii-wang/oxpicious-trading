"""llm_agents.deep_agents.core.authorization.service — the module's
ONE orchestrator (2026-11-13): run-start initialization, study-focus
extension, and late resolution, all fail-soft (an authorization hiccup
NEVER fails an LLM call — the worst case is the standing schema
allowlist, never a crashed run).

The two areas of work:

1. RUN START (:meth:`AuthorizationService.initialize_run`) — mint the
   run's THREAD TEMP-USER grant from the request's init scope: the
   UI's ticked tables + forced schemas (``data.forced_tables`` /
   ``forced_schemas`` via :class:`~llm_agents.deep_agents.run_input.\
RunInput`); a run carrying no picks (daemon / CLI / test) defaults to
   the standing schema allowlist.
2. SPAWN-TIME EXTENSION (:meth:`AuthorizationService.extend_node`) — a
   node EXPLICITLY given a study focus gets its KG-located tables
   unioned onto the parent scope (extend, never narrow) and one
   ``(chain, node)`` extension row; every other node inherits its
   parent's scope by construction (no row, no work).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from llm_agents.deep_agents.core.authorization.models import (
    DEFAULT_STANDING_SCHEMAS, AuthorizationContext, TablePermission,
    _normalize_names,
)
from llm_agents.deep_agents.core.authorization.store import (
    AUTHORIZATION_STORE, AuthorizationStore,
)

logger = logging.getLogger("deep_agents.core.authorization")

# How many KG-located tables one study focus may add (the same order as
# the data-hints lane's bound — an extension is a hint that became a
# grant).
EXTENSION_MAX_TABLES = 6


def _derive_init_scope(data: Optional[Dict[str, Any]],
                       ) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """(tables, schemas) from the run data — the typed boundary read.
    Empty schemas default to the standing allowlist (a table-tick scope
    with NO schema leg keeps the standing schemas as the fallback leg —
    ``allowed = table IN tables OR schema IN schemas`` stays true to the
    UI picks)."""
    tables: Tuple[str, ...] = ()
    schemas: Tuple[str, ...] = ()
    if data:
        try:
            from llm_agents.deep_agents.run_input import RunInput

            model = RunInput.model_validate(data)
            tables = _normalize_names(tuple(model.forced_tables))
            schemas = _normalize_names(tuple(model.forced_schemas))
        except Exception as exc:  # noqa: BLE001 — degrade to standing
            logger.warning("init scope boundary read failed: %s — the "
                           "standing schemas stand", exc)
    if not schemas:
        schemas = DEFAULT_STANDING_SCHEMAS
    return tables, schemas


class AuthorizationService:
    """The authorization module's orchestrator over the store + the KG.
    Fail-soft by contract: every method returns a usable context or
    scope even when its own writes fail."""

    def __init__(self, *,
                 store: Optional[AuthorizationStore] = None) -> None:
        self._store = store or AUTHORIZATION_STORE

    # -- 1. run start ---------------------------------------------------

    async def initialize_run(self, super_thread_id: str,
                             data: Optional[Dict[str, Any]],
                             ) -> AuthorizationContext:
        """The run's TEMP-USER context: derive the init scope from the
        request data, upsert the ``(chain, NULL)`` row, return the
        resolved context (returned even when the write fails — the gate
        enforces the derived scope regardless; the row is the audit
        record and the node-resolution fallback)."""
        tables, schemas = _derive_init_scope(data)
        permission = TablePermission(tables=tables, schemas=schemas)
        try:
            await self._store.ensure_thread_grant(super_thread_id,
                                                  permission)
        except Exception as exc:  # noqa: BLE001 — audit row only
            logger.warning("thread grant persist failed for %s: %s",
                           super_thread_id, exc)
        return AuthorizationContext.for_thread(super_thread_id, permission)

    # -- 2. spawn-time extension -----------------------------------------

    async def extend_node(self, thread_id: str, node_id: int,
                          focus: str,
                          parent: Optional[TablePermission] = None,
                          ) -> TablePermission:
        """One explicitly study-focused node's WIDENED scope: the parent
        permission (resolved from the chain's thread row when the caller
        carries none) UNION the tables the KG locates for *focus*, as
        ONE effective permission. Returns the PARENT unchanged when the
        focus adds nothing new (no row — inherit) or anything fails
        (extension never vetoes a spawn)."""
        focus = (focus or "").strip()
        if not focus:
            return parent if parent is not None else \
                TablePermission(schemas=DEFAULT_STANDING_SCHEMAS)
        try:
            if parent is None:
                chain = await self._store.resolve_chain_id(thread_id)
                parent = await self._store.fetch_effective(chain, None) \
                    or TablePermission(schemas=DEFAULT_STANDING_SCHEMAS)
            hits = await self._kg_tables(focus)
            fresh = tuple(t for t in hits
                          if not parent.allows(t))
            if not fresh:
                return parent
            merged = parent.extend(fresh, focus)
            chain = await self._store.resolve_chain_id(thread_id)
            await self._store.upsert_node_grant(chain, node_id, merged)
            logger.info("node %s scope extended by %d table(s) for focus "
                        "%r: %s", node_id, len(merged.added_tables), focus,
                        ", ".join(merged.added_tables))
            return merged
        except Exception as exc:  # noqa: BLE001 — inherit on failure
            logger.warning("node %s scope extension failed for focus %r "
                           "(inheriting the parent scope): %s: %s",
                           node_id, focus, type(exc).__name__, exc)
            return parent if parent is not None else \
                TablePermission(schemas=DEFAULT_STANDING_SCHEMAS)

    async def _kg_tables(self, focus: str) -> Tuple[str, ...]:
        """The KG-located tables for one focus, bounded and constrained
        to the standing study schemas (an extension widens the table
        list, never into the operational stores). The ns0 corpus also
        carries READ-SIDE VIEW entities whose names are sql_views
        TEMPLATE slash paths (``data_viz/api/.../price_vs_amt`` — query
        templates, not SELECTable relations): only schema-qualified
        relation names (``schema.name``) become granted tables."""
        from llm_agents.kg import KNOWLEDGE_GRAPHS

        hits = await KNOWLEDGE_GRAPHS.table_hits(
            focus, namespace=0, hops=1, max_results=EXTENSION_MAX_TABLES,
            allowed_schemas=DEFAULT_STANDING_SCHEMAS)
        return tuple(name for name in _normalize_names(tuple(hits))
                     if "." in name and "/" not in name)

    # -- 3. late resolution ------------------------------------------------

    async def resolve(self, thread_id: str,
                      node_id: Optional[int] = None,
                      ) -> Optional[TablePermission]:
        """A node's stored effective permission (its own row first, the
        thread row fallback) — None when the run has no row at all."""
        try:
            chain = await self._store.resolve_chain_id(thread_id)
            return await self._store.fetch_effective(chain, node_id)
        except Exception as exc:  # noqa: BLE001 — deny-by-default hint
            logger.warning("scope resolve failed for %s/%s: %s", thread_id,
                           node_id, exc)
            return None


# The process-wide service (the PLAN_STORE pattern).
AUTHORIZATION_SERVICE = AuthorizationService()
