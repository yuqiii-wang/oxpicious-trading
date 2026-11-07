"""llm_agents.kg.query_log — the llm.kg_queries search log / two-hit
evidence table behind the KG service (DDL
``database/sql/llm/14_kg_queries.sql``).

Every successful traversal is logged here: :meth:`QueryLog.log_chain`
replaces the (query, namespace) key's chain (DELETE + INSERT in one
transaction — at most one live chain per key, so a relog under a new
entity_hash drops the stale one). Since 2026-11-06 kg_search no longer
REPLAYS from this table — replays come from the promoted llm.kg_hotpaths
(:mod:`.hotpaths`) — instead it is the EVIDENCE half of the two-hit
promotion: :meth:`QueryLog.find_identical_chain` matches a fresh
traversal's packed chain against every chain logged here under the same
graph digest (any query text); an identical twin means the chain has
been produced twice, which promotes the query into kg_hotpaths.

Fail-soft by contract — the log must never break a search: a read
error reads as no evidence, a write error swallows after the live
result text is already built. Few rows per chain (≤ the packed edge
render cap), so plain asyncpg ``execute``/``executemany`` — no bulk
machinery. Connection: the MAIN async handle (the same llm-schema
choice :mod:`.service` makes), lazy-imported per the activation-order
rule.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence, Tuple

from _common.sql_writes import load_sql_write, render_sql_write

from llm_agents.kg.data_definitions import (
    CachedChain,
)




class QueryLog:
    """The llm.kg_queries reader/writer — stateless (one connection
    per call, fail-soft both ways)."""

    async def lookup_chain(self, query: str, namespace: int,
                           entity_hash: str) -> Optional[CachedChain]:
        """The logged chain for the exact (query, namespace) whose
        entity_hash matches the live graph — None on no row or ANY
        error (a broken cache read is a miss, never a failed search).
        The kg_queries read primitive (monitoring / probes): the
        replay lane itself reads llm.kg_hotpaths, not this table."""
        # Lazy import — the activation-order rule for _common.
        from _common.db_commons import get_db_connection_async

        try:
            conn = await get_db_connection_async()
            try:
                rows = await conn.fetch(
                    "SELECT chain_id, created_at, edge_id FROM llm.kg_queries "
                    "WHERE query = $1 AND namespace = $2 "
                    "AND entity_hash = $3 ORDER BY depth",
                    query, namespace, entity_hash)
            finally:
                await conn.close()
        except Exception:  # noqa: BLE001 — cache read fail-soft = miss
            return None
        if not rows:
            return None
        return CachedChain(
            chain_id=str(rows[0]["chain_id"]),
            created_at=rows[0]["created_at"],
            edge_ids=tuple(int(row["edge_id"]) for row in rows))

    async def log_chain(self, query: str, namespace: int, entity_hash: str,
                        edge_ids: Sequence[int],
                        *, chain_id: Optional[str] = None) -> str:
        """Log one successful search as its chain: DELETE the (query,
        namespace) key's previous chain, then INSERT one row per edge under
        ONE fresh uuid-hex chain_id — each row's depth is its 0-based
        ordinal among the chain's edges (the packing order the render
        used) — one transaction. Fail-soft: any error swallows (the caller
        already holds the live result text). Returns the chain_id used."""
        minted = chain_id or uuid.uuid4().hex
        if not edge_ids:
            return minted
        # Lazy import — the activation-order rule for _common.
        from _common.db_commons import get_db_connection_async

        try:
            conn = await get_db_connection_async()
            try:
                async with conn.transaction():
                    write = render_sql_write(
                        "llm_agents/kg/query_log/kg_queries/delete_for_key",
                        query, namespace)
                    await conn.execute(write.sql, *write.params)
                    await conn.executemany(
                        load_sql_write(
                            "llm_agents/kg/query_log/kg_queries/insert_rows"),
                        [(query, minted, int(edge_id), ordinal, namespace,
                          entity_hash)
                         for ordinal, edge_id in enumerate(edge_ids)])
            finally:
                await conn.close()
        except Exception:  # noqa: BLE001 — the log must never break a search
            pass
        return minted

    async def find_identical_chain(
            self, edge_ids: Sequence[int], namespace: int,
            entity_hash: str) -> Optional[CachedChain]:
        """The chain logged in llm.kg_queries — under ANY query text — whose
        packed edge list, in depth order, is IDENTICAL to *edge_ids* (same
        namespace, same entity_hash): the TWO-HIT evidence that promotes a
        query into llm.kg_hotpaths. The twin's own query text is not
        returned — only the fact that this chain has now been produced
        twice matters. None when no logged chain matches (or on ANY error
        — the promotion lane is fail-soft, same rule as the log).

        Call BEFORE :meth:`log_chain` relogs the fresh chain: a chain just
        logged for the same query would trivially match itself, promoting
        every first search."""
        if not edge_ids:
            return None
        # Lazy import — the activation-order rule for _common.
        from _common.db_commons import get_db_connection_async

        try:
            conn = await get_db_connection_async()
            try:
                row = await conn.fetchrow(
                    "SELECT chain_id, min(created_at) AS created_at, "
                    "array_agg(edge_id ORDER BY depth) AS edges "
                    "FROM llm.kg_queries "
                    "WHERE namespace = $1 AND entity_hash = $2 "
                    "GROUP BY chain_id "
                    "HAVING array_agg(edge_id ORDER BY depth) = $3::bigint[] "
                    "LIMIT 1",
                    namespace, entity_hash, [int(edge_id)
                                             for edge_id in edge_ids])
            finally:
                await conn.close()
        except Exception:  # noqa: BLE001 — evidence read fail-soft = no twin
            return None
        if row is None:
            return None
        return CachedChain(
            chain_id=str(row["chain_id"]),
            created_at=row["created_at"],
            edge_ids=tuple(int(edge_id) for edge_id in row["edges"]))
