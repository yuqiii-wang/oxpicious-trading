"""llm_agents.kg — the KNOWLEDGE-GRAPH domain as an independent module
(the DB-semantics + prompt-catalog graphs ``llm.kg_entities`` /
``llm.kg_edges`` as an online semantic search index).

Independent of the deep-agent harness: this package imports nothing
from ``llm_agents.deep_agents`` — the harness's ``kg_search`` /
``kg_expand`` TOOLS (``llm_agents/deep_agents/tools/kg_tools/kit.py``)
are thin adapters over :data:`KNOWLEDGE_GRAPHS`, the process-wide
:class:`KnowledgeGraphService` (schema + argument coercion + the
agent-flow hints stay there; everything domain lives here).

  graph/    — the pure traversal engine: seed recognition (bilingual
              token index, IDF-weighted) → spreading activation
              (per-hop decay, per-(relation, direction) factors,
              fan-out-normalized energy, sub-relation agreement boost,
              beam-bounded frontier) → blended rank → packed subgraph
              + the render functions (submodules: values = the frozen
              value types, vocabulary = the match vocabulary + tuning
              constants, engine = the KnowledgeGraph traversal,
              rendering = the bounded text blocks; all re-exported at
              ``llm_agents.kg.graph``);
  query_log.py — ``QueryLog``, the llm.kg_queries search LOG /
              two-hit evidence table (PK namespace, chain_id, edge_id
              — query NOT in the PK, indexed instead: (query,
              namespace) btree for the writer's replace-DELETE +
              trigram GIN): every successful traversal's chain is
              logged fail-soft (one chain_id, one row per packed
              edge, each row's depth = its edge's 0-based ordinal —
              hops is not identity), and ``find_identical_chain``
              matches a fresh traversal against every logged chain
              under the same graph digest (any query text) — the
              TWO-HIT evidence that promotes;
  hotpaths.py — ``HotpathCache``, the llm.kg_hotpaths PROMOTED replay
              layer (same row shape as kg_queries plus hit_count):
              replays come ONLY from here — ``lookup`` exact-matches
              the (query, namespace) under the live entity digest and
              bumps hit_count; ``promote`` lands a twice-produced
              chain at hit_count = 2 (DELETE + INSERT, one live hot
              chain per key);
  service.py — ``KnowledgeGraphService``: the LOAD (TTL-cached
              per-namespace graph), the SEARCH (the two-layer
              chain-cache flow, rendered), the EXPAND (one node's
              neighborhood) + ``NS_SCOPES`` (the namespace vocabulary)
              and ``KgUnavailableError``.
"""
from llm_agents.kg.graph import (
    KgEdge, KgEntity, KnowledgeGraph, SeedHit, SearchResult, WalkHit,
)
from llm_agents.kg.hotpaths import (
    PROMOTION_HIT_COUNT, HotpathCache, HotpathChain,
)
from llm_agents.kg.query_log import CachedChain, QueryLog
from llm_agents.kg.service import (
    KG_CACHE_TTL_SEC, NS_SCOPES, KgUnavailableError, KnowledgeGraphService,
)

# The process-wide service — the TTL graph cache is only useful shared
# (every conversation of a run, the supervisor kit, the probes).
KNOWLEDGE_GRAPHS = KnowledgeGraphService()

__all__ = [
    "CachedChain", "HotpathCache", "HotpathChain", "KG_CACHE_TTL_SEC",
    "KNOWLEDGE_GRAPHS", "KgEdge", "KgEntity", "KgUnavailableError",
    "KnowledgeGraph", "KnowledgeGraphService", "NS_SCOPES",
    "PROMOTION_HIT_COUNT", "SeedHit", "SearchResult", "QueryLog", "WalkHit",
]
