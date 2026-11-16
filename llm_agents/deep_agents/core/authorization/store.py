"""llm_agents.deep_agents.core.authorization.store — the persistence
face over ``llm.deep_agent_authorizations`` (2026-11-13).

One choke-point class (:class:`AuthorizationStore`) for every read and
write of the table, mirroring the planning store's discipline: one
short-lived asyncpg connection per call (``_common.db_commons``, lazily
imported per the activation-order rule), all SQL from the
``database/sql_writes`` action files + the ``database/sql_views``
templates whose mirror paths ARE this package's path, every method
fail-soft by contract at the CALLER (the service) — a failed
authorization write never fails an LLM call.
"""
from __future__ import annotations

import logging
from typing import Optional

from _common.sql_views import render_sql_view
from _common.sql_writes import render_sql_write

from llm_agents.deep_agents.core.authorization.models import (
    TablePermission,
)

logger = logging.getLogger("deep_agents.core.authorization")

_VIEW_FETCH = ("llm_agents/deep_agents/core/authorization/"
               "deep_agent_authorizations/fetch_scope")
_VIEW_CHAIN = ("llm_agents/deep_agents/core/authorization/"
               "deep_agent_authorizations/resolve_chain")


class AuthorizationStore:
    """Every read/write of ``llm.deep_agent_authorizations``: the
    thread temp-user grant, the node extension grants, the effective
    resolution (node row first, thread row fallback) and the chain
    purge."""

    async def _connect(self):
        from _common.db_commons import get_db_connection_async

        return await get_db_connection_async()

    async def ensure_thread_grant(self, super_thread_id: str,
                                  permission: TablePermission) -> None:
        """Upsert the run's THREAD TEMP-USER row (node_id NULL) — the
        init scope every un-rowed node falls back to. Idempotent: a
        re-run refreshes the permission."""
        conn = await self._connect()
        try:
            write = render_sql_write(
                "llm_agents/deep_agents/core/authorization/"
                "deep_agent_authorizations/insert_thread",
                super_thread_id, permission.to_json(),
            )
            await conn.execute(write.sql, *write.params)
        finally:
            await conn.close()

    async def upsert_node_grant(self, super_thread_id: str, node_id: int,
                                permission: TablePermission) -> None:
        """Upsert ONE node's extension row — the parent scope UNION the
        KG-located delta, materialized at write time."""
        conn = await self._connect()
        try:
            write = render_sql_write(
                "llm_agents/deep_agents/core/authorization/"
                "deep_agent_authorizations/upsert_node",
                super_thread_id, int(node_id), permission.to_json(),
            )
            await conn.execute(write.sql, *write.params)
        finally:
            await conn.close()

    async def fetch_effective(self, super_thread_id: str,
                              node_id: Optional[int],
                              ) -> Optional[TablePermission]:
        """Resolve a node's effective permission: its OWN extension row
        when it has one, else the thread temp-user row. None when the
        run has no row at all (deny-by-default at the caller)."""
        conn = await self._connect()
        try:
            view = render_sql_view(_VIEW_FETCH,
                                   super_thread_id=super_thread_id,
                                   node_id=node_id)
            rows = await conn.fetch(view.sql, *view.params)
        finally:
            await conn.close()
        node_rows = [r for r in rows if r["node_id"] is not None]
        thread_rows = [r for r in rows if r["node_id"] is None]
        for row in node_rows + thread_rows:
            return TablePermission.from_doc(row["permission"])
        return None

    async def resolve_chain_id(self, thread_id: str) -> str:
        """Any segment thread id -> the run's CHAIN id (the
        authorization key): the states' super_thread_id, the thread's
        own id for legacy single-thread runs, the given id when no
        state row exists yet (a spawn before its parent's row?)."""
        conn = await self._connect()
        try:
            view = render_sql_view(_VIEW_CHAIN, thread_id=thread_id)
            value = await conn.fetchval(view.sql, *view.params)
        finally:
            await conn.close()
        return str(value) if value else thread_id

    async def delete_for_thread(self, super_thread_id: str) -> None:
        """The chain purge: the temp-user row AND every extension row
        of one run (kept until the run itself goes — audit records)."""
        conn = await self._connect()
        try:
            write = render_sql_write(
                "llm_agents/deep_agents/core/authorization/"
                "deep_agent_authorizations/delete_for_thread",
                super_thread_id,
            )
            await conn.execute(write.sql, *write.params)
        finally:
            await conn.close()


# The process-wide store (the PLAN_STORE pattern — one instance, no
# pools; the write volume is a handful of rows per run).
AUTHORIZATION_STORE = AuthorizationStore()
