"""llm_agents.kg.hotpaths — llm.kg_hotpaths, the PROMOTED hot-path
layer of the kg_search chain cache (DDL
``database/sql/llm/16_kg_hotpaths.sql``).

kg_search replays from THIS table, not from llm.kg_queries: a query
lands here only after its result chain has proven REPEATABLE — the
TWO-HIT rule. A hotpaths miss runs the live traversal, whose fresh
packed chain :meth:`QueryLog.find_identical_chain` matches against
every chain logged in llm.kg_queries under the same graph digest (any
query text); an identical twin means the chain has been produced twice
(the logged hit + the fresh hit) and :meth:`HotpathCache.promote`
inserts it here with hit_count = 2. Every replay of a hot path bumps
its hit_count (:meth:`HotpathCache.lookup` reads the chain and counts
the hit in one transaction), so hit_count tracks how often the path
has been served; a re-promotion under a new entity_hash resets it.

Fail-soft by contract — the same rule as query_log: a read error reads
as a miss, a write error swallows after the live result text is
already built. Few rows per chain (≤ the packed edge render cap), so
plain asyncpg ``execute``/``executemany`` — no bulk machinery.
Connection: the MAIN async handle (the same llm-schema choice
:mod:`.service` makes), lazy-imported per the activation-order rule.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional, Sequence

from _common.sql_writes import load_sql_write, render_sql_write
from llm_agents.kg.query_log import CachedChain

from llm_agents.kg.data_definitions import (
    HotpathChain,
)
# hit_count a promotion is seeded with — the two identical productions
# (the chain logged in kg_queries + the fresh traversal that matched
# it) that earned the query its hot path.
PROMOTION_HIT_COUNT = 2




class HotpathCache:
    """The llm.kg_hotpaths reader/writer behind kg_search's replay
    lane — stateless (one connection per call, fail-soft both ways)."""

    async def lookup(self, query: str, namespace: int,
                     entity_hash: str) -> Optional[HotpathChain]:
        """The hot path for the exact (query, namespace) whose
        entity_hash matches the live graph — None on no row or ANY
        error (a broken cache read is a miss, never a failed search).
        A hit COUNTS: hit_count is bumped on every row of the chain in
        the same transaction, and the returned chain's ``hit_count``
        already includes this replay."""
        # Lazy import — the activation-order rule for _common.
        from _common.db_commons import get_db_connection_async

        try:
            conn = await get_db_connection_async()
            try:
                async with conn.transaction():
                    rows = await conn.fetch(
                        "SELECT chain_id, created_at, edge_id, "
                        "hit_count FROM llm.kg_hotpaths "
                        "WHERE query = $1 AND namespace = $2 "
                        "AND entity_hash = $3 ORDER BY depth",
                        query, namespace, entity_hash)
                    if rows:
                        write = render_sql_write(
                            "llm_agents/kg/hotpaths/kg_hotpaths/"
                            "bump_hit_count",
                            query, namespace)
                        await conn.execute(write.sql, *write.params)
            finally:
                await conn.close()
        except Exception:  # noqa: BLE001 — cache read fail-soft = miss
            return None
        if not rows:
            return None
        return HotpathChain(
            chain_id=str(rows[0]["chain_id"]),
            created_at=rows[0]["created_at"],
            edge_ids=tuple(int(row["edge_id"]) for row in rows),
            hit_count=int(rows[0]["hit_count"]) + 1)

    async def promote(self, query: str, namespace: int, entity_hash: str,
                      edge_ids: Sequence[int]) -> None:
        """Promote one proven-repeatable chain into the hot paths:
        DELETE the (query, namespace) key's previous rows, then INSERT
        one row per edge under ONE fresh uuid-hex chain_id with
        hit_count = PROMOTION_HIT_COUNT (the two identical productions
        that earned the promotion) — one transaction, each row's depth
        its 0-based ordinal among the chain's edges. Fail-soft: any
        error swallows (the caller already holds the live result
        text)."""
        if not edge_ids:
            return
        minted = uuid.uuid4().hex
        # Lazy import — the activation-order rule for _common.
        from _common.db_commons import get_db_connection_async

        try:
            conn = await get_db_connection_async()
            try:
                async with conn.transaction():
                    write = render_sql_write(
                        "llm_agents/kg/hotpaths/kg_hotpaths/delete_for_key",
                        query, namespace)
                    await conn.execute(write.sql, *write.params)
                    await conn.executemany(
                        load_sql_write(
                            "llm_agents/kg/hotpaths/kg_hotpaths/insert_rows"),
                        [(query, minted, int(edge_id), ordinal,
                          namespace, entity_hash, PROMOTION_HIT_COUNT)
                         for ordinal, edge_id in enumerate(edge_ids)])
            finally:
                await conn.close()
        except Exception:  # noqa: BLE001 — the log must never break a search
            pass
