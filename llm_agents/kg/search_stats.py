"""llm_agents.kg.search_stats — llm.kg_search_stats, the per-call
search ledger behind the KG service (DDL
``database/sql/llm/kg_search_stats.sql``).

The chain tables (llm.kg_queries / llm.kg_hotpaths) record only
SUCCESSFUL, non-empty live traversals, so a no-match search left no
trace and no score was ever persisted. This ledger records EVERY
answered kg_search call — the hot-path replay, the live search (match
or no-match) and the gate-rejected result — as one
:class:`KgSearchStatRow`: the ranked node names + scores, the rung
counters (``graph.SearchTrace`` — what the seed / walk rungs produced
and what the relevance floors pruned), the replay and gate facts, and
the latency. The row IS the call's accumulator:
``KnowledgeGraphService._search`` opens it with the call's parameters
and the answer path fills it in as it serves (``note_result`` /
``note_replay`` + the replay / gate / promotion facts); the latency
lands last and :class:`SearchStatsLog` writes it AFTER the result text
is built.

Fail-soft by contract — the same rule as query_log / hotpaths: a write
error swallows (one warning per half hour, then debug) and never
reaches the search result. A pure side ledger: nothing in the search
path reads it, and a miss is never logged as a chain or promoted. One
row per call, so plain asyncpg ``execute`` — no bulk machinery.
Connection: the MAIN async handle (the same llm-schema choice
:mod:`.service` makes), lazy-imported per the activation-order rule.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional, Sequence, Tuple

from pydantic import BaseModel

from _common.sql_writes import render_sql_write

from llm_agents.kg.graph.values import KgEntity, SearchResult

logger = logging.getLogger(__name__)

# The write action (database/sql_writes/llm_agents/kg/search_stats/
# kg_search_stats/insert_row.sql) — the row's field order is its $n order.
_ACTION = "llm_agents/kg/search_stats/kg_search_stats/insert_row"

# How many ranked nodes one row names (the head of the ranking).
TOP_NODES_MAX = 10


class KgSearchStatRow(BaseModel):
    """One ``llm.kg_search_stats`` row — the typed record of ONE
    kg_search call on any return path, and that call's accumulator:
    the service opens it with the call's parameters and fills it in as
    it serves. FIELD ORDER IS THE WRITE ACTION'S PARAMETER ORDER
    (``insert_row.sql`` $1..$30); the action file's header documents
    every field. The engine's UNFILTERED result is what ``note_result``
    records, even when the relevance gate later filters the render —
    the counters and ``top_nodes`` describe what the traversal found."""

    query: str
    namespace: int
    full_detail: bool
    hops: int
    max_results: int
    forced_scope: bool = False
    entity_hash: str = ""
    replayed: bool = False
    replay_chain_id: Optional[str] = None
    replay_exact: Optional[bool] = None
    replay_similarity: Optional[float] = None
    gate_read: bool = False
    gate_rejected: bool = False
    gate_accepted: bool = False
    promoted: bool = False
    n_nodes: int = 0
    n_tables: int = 0
    n_edges: int = 0
    n_tokens: int = 0
    n_seeds: int = 0
    n_seed_kept: int = 0
    n_seed_pruned: int = 0
    n_walk: int = 0
    n_walk_kept: int = 0
    n_walk_pruned: int = 0
    n_fuzzy_tokens: int = 0
    seed_top: Optional[float] = None
    walk_top: Optional[float] = None
    top_nodes: str = "[]"
    latency_ms: float = 0.0

    @staticmethod
    def is_template(name: str, entity_type: str) -> bool:
        """A read-side sql_views template — the slash-path ``view``
        entities (vocabulary.seed_type_damp's own predicate): damped
        locator-lane enrichment, not a table-class answer."""
        return entity_type == "view" and "/" in name

    @staticmethod
    def n_tables_of(entities: Sequence[KgEntity]) -> int:
        """The table-class entities of a ranking — everything but the
        sql_views templates."""
        return sum(1 for entity in entities
                   if not KgSearchStatRow.is_template(
                       entity.name, entity.entity_type))

    @staticmethod
    def _head(entries: Sequence[Tuple[str, str, float]]) -> str:
        return json.dumps(
            [{"name": name, "kind": kind, "score": round(float(score), 3)}
             for name, kind, score in entries[:TOP_NODES_MAX]],
            ensure_ascii=False)

    def note_result(self, result: SearchResult) -> None:
        """Fill this row from the ENGINE result a live call served: the
        node / edge counts, the bounded ranked head (``[{name, kind,
        score}, …]``) and the rung counters of its ``SearchTrace``."""
        trace = result.trace
        self.n_nodes = len(result.nodes)
        self.n_tables = self.n_tables_of(
            [node.entity for node in result.nodes])
        self.n_edges = len(result.edges)
        self.top_nodes = self._head(
            [(node.entity.name, node.entity.entity_type, node.score)
             for node in result.nodes])
        if trace is None:
            return
        self.n_tokens = trace.n_tokens
        self.n_seeds = trace.n_seeds
        self.n_seed_kept = trace.n_seed_kept
        self.n_seed_pruned = trace.n_seed_pruned
        self.n_walk = trace.n_walk
        self.n_walk_kept = trace.n_walk_kept
        self.n_walk_pruned = trace.n_walk_pruned
        self.n_fuzzy_tokens = trace.n_fuzzy_tokens
        self.seed_top = trace.seed_top if trace.n_seeds else None
        self.walk_top = trace.walk_top if trace.n_walk else None

    def note_replay(self, chain_id: str, members: Sequence[KgEntity],
                    n_edges: int, *, exact: Optional[bool] = True,
                    similarity: Optional[float] = None) -> None:
        """Fill this row from a hot-path REPLAY: the served chain, its
        DISTINCT member entities in chain order at score 0 (a replay
        carries no scores — the chain, not a ranking, is its identity)
        and its resolved edge count. The rung counters stay 0 — no
        traversal ran. *exact* / *similarity* say how the hot path was
        matched (the exact query text by default)."""
        self.replayed = True
        self.replay_chain_id = chain_id
        self.replay_exact = exact
        self.replay_similarity = similarity
        self.n_nodes = len(members)
        self.n_tables = self.n_tables_of(members)
        self.n_edges = n_edges
        self.top_nodes = self._head(
            [(entity.name, entity.entity_type, 0.0)
             for entity in members])

    def params(self) -> Tuple[object, ...]:
        """The write action's positional arguments, in its ``$1..$n``
        order — the model's field order."""
        return tuple(self.model_dump().values())


class SearchStatsLog:
    """The llm.kg_search_stats writer — stateless (one connection per
    call), fail-soft both ways: :meth:`record` returns whether the row
    landed and never raises."""

    # A missing table / a DB outage must not flood the service log (the
    # caution_log policy): one warning per window, then debug lines.
    _WARN_EVERY_SEC = 1800.0

    def __init__(self) -> None:
        self._last_warn = 0.0

    async def record(self, row: KgSearchStatRow) -> bool:
        """Insert one call's stats row. Fail-soft: any error swallows
        (the caller already holds the result text)."""
        # Lazy import — the activation-order rule for _common.
        from _common.db_commons import get_db_connection_async

        try:
            write = render_sql_write(_ACTION, *row.params())
            conn = await get_db_connection_async()
            try:
                await conn.execute(write.sql, *write.params)
            finally:
                await conn.close()
            return True
        except Exception as exc:  # noqa: BLE001 — the ledger never breaks a search
            now = time.monotonic()
            level = (logging.WARNING
                     if now - self._last_warn >= self._WARN_EVERY_SEC
                     else logging.DEBUG)
            if level == logging.WARNING:
                self._last_warn = now
            logger.log(level, "kg_search_stats write failed for "
                              "'%s' (ns%d): %s: %s", row.query,
                       row.namespace, type(exc).__name__, exc)
            return False
