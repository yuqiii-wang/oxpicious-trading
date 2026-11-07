"""llm_agents.deep_agents.planning.store.chain — the CHAIN READS /
PURGES / SETTLE (the ChainStoreMixin).

Since the three-graph refactor ONE RUN = THREE chained graphs, ONE
THREAD EACH, chained by ``llm.deep_agent_states.super_thread_id`` (=
the root plan thread's own id, stamped on every row of P/C/R; legacy
single-thread runs read as their own chain via the COALESCE in the
chain-scope SQL). The reads here are the chain-scoped reads —
``fetch_chain_threads`` / ``list_super_states`` / ``purge_super_
thread`` / ``settle_chain`` — the runner's result, the supervisor
session and the UI's chain canvas run on; plus the context-tags
backfill heal (the search lanes' never-miss-own-traffic contract).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import List, Optional, Sequence

from _common.sql_writes import load_sql_write, render_sql_write

from llm_agents.deep_agents.planning.store.base import _StoreBase
from llm_agents.deep_agents.planning.store.models import (
    _STATE_COLUMNS, _tokenize_tags_sync, StateRow,
)
from llm_agents.deep_agents.planning.store.outputs import OutputStoreMixin

logger = logging.getLogger("deep_agents.plan_store")


class ChainStoreMixin(OutputStoreMixin):
    """The chain-scoped reads, purges, settle + the tags backfill.
    (Builds on the output mixin — ``settle_chain`` drives
    ``finalize_thread``.)"""

    async def list_thread_states(self, thread_id: str) -> List[StateRow]:
        """The thread's TOP-LEVEL states in plan order (children excluded —
        load them by parent_state_id when needed)."""
        conn = await self._connect()
        try:
            records = await conn.fetch(
                f"SELECT {_STATE_COLUMNS} FROM llm.deep_agent_states "
                f"WHERE thread_id = $1 AND parent_state_id IS NULL "
                f"ORDER BY ordinal, state_id",
                thread_id,
            )
        finally:
            await conn.close()
        return [self._state_row(r) for r in records]


    async def fetch_chain_threads(self, thread_id: str) -> List[str]:
        """The CHAIN's thread ids in chain order (plan → custom → result),
        resolved from any member's id: every row of the run carries the
        root's id in super_thread_id, so the members are the distinct
        thread_ids sharing it (legacy single-thread runs resolve to
        themselves — the root's own rows carry its id). Ordered by each
        member's earliest row creation."""
        conn = await self._connect()
        try:
            records = await conn.fetch(
                "SELECT thread_id, MIN(created_at) AS first_at "
                "FROM llm.deep_agent_states "
                "WHERE super_thread_id = $1 OR thread_id = $1 "
                "GROUP BY thread_id ORDER BY first_at, thread_id",
                thread_id)
        finally:
            await conn.close()
        return [str(r["thread_id"]) for r in records]


    async def list_super_states(self, super_thread_id: str) -> List[StateRow]:
        """The WHOLE CHAIN's top-level states in chain order — the plan
        thread's plan row, then the custom graph's steps, then the result
        graph's review seats, each segment in plan order (the super-scoped
        read the runner's result, the supervisor session and the UI's
        response snapshot use; legacy runs resolve to their single thread)."""
        out: List[StateRow] = []
        for member in await self.fetch_chain_threads(super_thread_id):
            out.extend(await self.list_thread_states(member))
        return out


    async def purge_super_thread(self, thread_id: str) -> None:
        """Remove ONE RUN'S WHOLE CHAIN (the three-graph refactor: the
        delete crosses every thread the run minted — the plan thread the
        caller names and every member sharing its super_thread_id; legacy
        single-thread runs resolve to themselves). Each member purges in
        the established order (llm_requests chains first, then states /
        contexts / identities / store items — see :func:`purge_thread`).
        Plan templates saved from the run are deliberately NOT touched (a
        template outlives its source run)."""
        for member in await self.fetch_chain_threads(thread_id):
            await self.purge_thread(member)


    async def purge_thread(self, thread_id: str) -> None:
        """Remove one thread entirely (its llm_requests chains first — a
        request's chain_id is the node's llm_context_id, resolvable only
        while the linking rows exist — then states, whose self-FK cascades
        children and whose contexts.state_id CASCADE removes the
        task-linked context rows, then the thread's remaining contexts
        (final-result and unlinked rows), its identity link and its store
        items). The bridge's DELETE /api/ai/summary-runs/:thread_id and
        09_deep_agent_runs_prune.sql follow the same order."""
        from llm_agents.deep_agents.langgraph.state_store import delete_thread_items

        conn = await self._connect()
        try:
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "llm_requests/delete_chains_for_thread",
                thread_id)
            await conn.execute(write.sql, *write.params)
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_states/delete_for_thread", thread_id)
            await conn.execute(write.sql, *write.params)
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_contexts/delete_for_thread", thread_id)
            await conn.execute(write.sql, *write.params)
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_identities/delete_for_thread", thread_id)
            await conn.execute(write.sql, *write.params)
        finally:
            await conn.close()
        await delete_thread_items(thread_id)


    async def backfill_context_tags_async(self,
        conn=None, *, thread_ids: Optional[Sequence[str]] = None,
        chunk_rows: int = 200,
    ) -> int:
        """Heal context rows whose tags IS NULL — tokenize their stored
        payloads and set the vector (the mirror of request_log.
        backfill_search_tsv_async; the NULL filter is the work queue, so
        the pass is resumable). *thread_ids* scopes the heal to one run's
        threads (the thread-internal search heals its chain's scope before
        searching); None heals the whole table. Returns the number of rows
        indexed; a failure raises to the caller (the tool path renders it
        as tool text)."""
        from _common.db_commons import get_db_connection_async

        own = conn is None
        if own:
            conn = await get_db_connection_async()
        total = 0
        try:
            while True:
                rows = await conn.fetch(
                    """SELECT context_id, payload::text AS payload
                         FROM llm.deep_agent_contexts
                        WHERE tags IS NULL"""
                    + (" AND thread_id = ANY($2::text[])" if thread_ids else "")
                    + " LIMIT $1",
                    *([chunk_rows, list(thread_ids)] if thread_ids
                      else [chunk_rows]))
                if not rows:
                    break
                tokens = await asyncio.gather(*(
                    asyncio.to_thread(_tokenize_tags_sync, row["payload"])
                    for row in rows))
                await conn.executemany(
                    load_sql_write(
                        "llm_agents/deep_agents/planning/store/"
                        "deep_agent_contexts/backfill_tags"),
                    [(row["context_id"], tok)
                     for row, tok in zip(rows, tokens)])
                total += len(rows)
                await asyncio.sleep(0.1)
        finally:
            if own and conn is not None:
                with contextlib.suppress(Exception):
                    await conn.close()
        if total:
            logger.info("context tags backfill: %d row(s) indexed",
                        total)
        return total

    async def settle_chain(
        self, thread_id: str, *, failure: Optional[str] = None
    ) -> List[StateRow]:
        """Settle the run's WHOLE CHAIN (in_progress -> failed, pending ->
        skipped) and return the settled chain-order snapshot — any member
        id resolves the chain (legacy single-thread runs resolve to
        themselves). Each graph's finalize node settles its own thread at
        its end; the server's /stop path runs this directly when a run was
        cancelled mid-chain (the graphs never reach their finalize then —
        the settle keeps the audit trail closed)."""
        plan_rows: List[StateRow] = []
        for member in await self.fetch_chain_threads(thread_id):
            await self.finalize_thread(member, failure=failure)
            plan_rows.extend(await self.list_thread_states(member))
        return plan_rows
