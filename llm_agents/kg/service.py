"""llm_agents.kg.service — ``KnowledgeGraphService``, the one face of
the knowledge-graph domain: namespace graphs loaded TTL-cached from
``llm.kg_entities`` / ``llm.kg_edges``, searched through the TWO-LAYER
chain cache, rendered to the compact tool text.

Independent of the deep-agent harness by design (the harness's
``kg_search`` / ``kg_expand`` tools in
``llm_agents/deep_agents/tools/kg_tools/kit.py`` are thin adapters
over this class — schema + argument coercion + the agent-flow hints;
everything domain lives HERE). The service owns:

  * the LOAD — one read pair per namespace (entities + edges), turned
    into one in-memory :class:`~llm_agents.kg.graph.KnowledgeGraph`
    (adjacency + token index built once at load). The graph is STATIC
    SEED DATA — rows change only when a seed file is applied — so the
    loader caches per namespace with a TTL (default 10 min): every
    conversation of a run shares one graph without a DB round trip
    per call, and an applied seed shows up on the next run without a
    daemon restart. The connection is the MAIN async handle (not the
    read replica): the graph is llm-schema metadata whose freshness
    matters right after a seed apply.
  * the SEARCH — the two-layer chain-cache flow: replay from the
    promoted ``llm.kg_hotpaths`` (hit_count bumped) → else the live
    traversal, its fresh chain matched against every chain logged in
    ``llm.kg_queries`` under the same graph digest (any query text,
    BEFORE the relog) → an identical twin promotes the query at
    hit_count 2; the fresh chain is relogged either way (both layers
    fail-soft — a cache error never breaks a search).
  * the EXPAND — one node's neighborhood (the drill-down face over
    the same graphs).
"""
from __future__ import annotations

import time
from typing import Dict, Sequence, Tuple

from llm_agents.kg.graph import (
    NODE_MAX_CAP, NODE_MAX_DEFAULT, KgEdge, KgEntity, KnowledgeGraph,
    render_expand, render_replay, render_search,
)
from llm_agents.kg.hotpaths import HotpathCache
from llm_agents.kg.query_log import QueryLog

# The static-seed TTL: seconds one loaded graph serves before the next
# call re-reads the tables.
KG_CACHE_TTL_SEC = 600.0


class KgUnavailableError(Exception):
    """The namespace's graph could not be loaded (DB error or an
    unseeded namespace) — a deployment problem the caller should
    surface, not silently serve around."""


# The per-namespace vocabulary the renders carry: the scope label for
# headers (the agent-flow hint AFTER a hit is tool-layer vocabulary —
# ns0 walks into columns/data, ns1 loads a skill — and stays with the
# adapters in the deep-agent kit).
NS_SCOPES: Dict[int, str] = {
    0: "ns0 DB semantics: tables/views and how they relate",
    1: "ns1 prompt catalog: skills & plan prompts",
}


class KnowledgeGraphService:
    """The KG domain service — per-process state is the TTL graph
    cache (share the :data:`KNOWLEDGE_GRAPHS` singleton; a fresh
    instance only pays re-reading the tables)."""

    def __init__(self, *, cache_ttl_sec: float = KG_CACHE_TTL_SEC) -> None:
        self._cache_ttl_sec = cache_ttl_sec
        # namespace -> (loaded_at_monotonic, graph)
        self._graphs: Dict[int, Tuple[float, KnowledgeGraph]] = {}
        self._log = QueryLog()
        self._hotpaths = HotpathCache()

    # -- the load -------------------------------------------------------

    async def graph(self, namespace: int = 0) -> KnowledgeGraph:
        """The namespace's graph, cached for the service TTL. Raises
        :class:`KgUnavailableError` on a DB error or an EMPTY graph (an
        unseeded namespace is a deployment problem to surface)."""
        cached = self._graphs.get(namespace)
        now = time.monotonic()
        if cached is not None and now - cached[0] < self._cache_ttl_sec:
            return cached[1]
        # Lazy import — the activation-order rule the llm_agents modules
        # follow for _common.
        from _common.db_commons import get_db_connection_async

        try:
            conn = await get_db_connection_async()
            try:
                entity_rows = await conn.fetch(
                    "SELECT entity_id, name, entity_type, description, "
                    "tags FROM llm.kg_entities WHERE namespace = $1",
                    namespace)
                edge_rows = await conn.fetch(
                    "SELECT edge_id, relation, sub_relation, entity_ids "
                    "FROM llm.kg_edges WHERE namespace = $1", namespace)
            finally:
                await conn.close()
        except Exception as exc:
            raise KgUnavailableError(
                f"knowledge graph namespace {namespace} could not be "
                f"read: {exc}") from exc
        if not entity_rows:
            raise KgUnavailableError(
                f"knowledge graph namespace {namespace} is empty — the "
                "seed files under database/sql/llm/static/knowledge_graphs/ "
                "have not been applied (temp_scripts/apply_kg.py)")
        graph = KnowledgeGraph(
            entities=(KgEntity(
                entity_id=int(row["entity_id"]),
                name=str(row["name"]),
                entity_type=str(row["entity_type"]),
                description=str(row["description"] or ""),
                tags=tuple(str(tag) for tag in (row["tags"] or ())),
            ) for row in entity_rows),
            edges=(KgEdge(
                edge_id=int(row["edge_id"]),
                relation=str(row["relation"]),
                sub_relation=(str(row["sub_relation"])
                              if row["sub_relation"] is not None else None),
                member_ids=tuple(int(member) for member in row["entity_ids"]),
            ) for row in edge_rows))
        self._graphs[namespace] = (now, graph)
        return graph

    # -- the search -----------------------------------------------------

    async def search(self, query: str, *, namespace: int = 0,
                     hops: int = 2,
                     max_results: int = NODE_MAX_DEFAULT) -> str:
        """One kg_search answered: the two-layer chain-cache flow over
        the namespace's graph, rendered to the compact tool text (the
        caller — the deep-agent adapter — appends its agent-flow hint).

        The llm.kg_hotpaths HOT-PATH replay layer: kg_search replays
        ONLY proven-repeatable chains. The exact-match lookup hits
        llm.kg_hotpaths (a query promoted after its chain was produced
        identically TWICE) and bumps its hit_count; a miss — or a chain
        whose logged edges no longer resolve — runs the live traversal.
        The fresh chain is then matched against EVERY chain logged in
        llm.kg_queries under the same graph digest (any query text,
        BEFORE the relog — a just-logged chain would match itself): an
        identical twin means the chain has TWO hits, and the query is
        promoted into kg_hotpaths with hit_count 2. The relog to
        kg_queries happens either way (both layers fail-soft).

        Raises :class:`KgUnavailableError` when the graph cannot load,
        ``ValueError`` on an empty query or an unknown namespace."""
        query = query.strip()
        if not query:
            raise ValueError("pass business keywords (e.g. '动量 momentum' "
                             "or 'margin 融资融券')")
        if namespace not in NS_SCOPES:
            raise ValueError(
                f"namespace must be one of {sorted(NS_SCOPES)}")
        hops = max(1, min(3, int(hops)))
        max_results = max(4, min(NODE_MAX_CAP, int(max_results)))
        kg = await self.graph(namespace)
        hot = await self._hotpaths.lookup(query, namespace,
                                          kg.entity_digest)
        if hot is not None:
            edges: Tuple[KgEdge, ...] = tuple(
                edge for edge in (kg.edge(edge_id)
                                  for edge_id in hot.edge_ids)
                if edge is not None)
            if edges:
                return render_replay(
                    kg, query, edges,
                    chain_id=hot.chain_id,
                    created_at=hot.created_at,
                    hit_count=hot.hit_count,
                    scope_label=NS_SCOPES[namespace])
        result = kg.search(query, hops=hops, max_nodes=max_results)
        if result.edges:
            edge_ids = tuple(edge.edge_id for edge in result.edges)
            twin = await self._log.find_identical_chain(
                edge_ids, namespace, kg.entity_digest)
            await self._log.log_chain(
                query, namespace, kg.entity_digest, edge_ids)
            if twin is not None:
                await self._hotpaths.promote(
                    query, namespace, kg.entity_digest, edge_ids)
        return render_search(kg, query, result, hops=hops,
                             scope_label=NS_SCOPES[namespace])

    # -- the expand -----------------------------------------------------

    async def expand(self, names: Sequence[str], *, namespace: int = 0,
                     hops: int = 1) -> str:
        """One kg_expand answered: the named nodes' neighborhood over
        the namespace's graph, rendered to the compact tool text.
        Raises :class:`KgUnavailableError` when the graph cannot load,
        ``ValueError`` on no names or an unknown namespace."""
        names = [str(name).strip() for name in names if str(name).strip()]
        if not names:
            raise ValueError("pass name(s), e.g. "
                             "['analysis.mov_ave_rsi'] (ns0) or ['pe-guide'] "
                             "(ns1)")
        if namespace not in NS_SCOPES:
            raise ValueError(
                f"namespace must be one of {sorted(NS_SCOPES)}")
        hops = max(1, min(2, int(hops)))
        kg = await self.graph(namespace)
        resolved, edges, neighbors, unresolved = kg.neighborhood(
            names, hops=hops)
        if not resolved:
            raise ValueError(
                f"none of {names} resolved in namespace {namespace} — "
                "use kg_search to find the right name first")
        return render_expand(kg, resolved, edges, neighbors, unresolved,
                             hops=hops)
