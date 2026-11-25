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
              neighborhood), the COLUMN AUTO-LOAD (retrieved table
              entities carry their exact column names + comments —
              columns.py) + ``NS_SCOPES`` (the namespace vocabulary)
              and ``KgUnavailableError``;
  search_stats.py — ``SearchStatsLog`` / ``KgSearchStatRow``, the
              llm.kg_search_stats PER-CALL search ledger (2026-11-23):
              one fail-soft row per ANSWERED kg_search call on every
              return path — a replay, a live match, a no-match, a
              gate-rejected result (the chain tables only ever saw
              successful non-empty traversals) — carrying the ranked
              node names + scores, the rung counters
              (``graph.SearchTrace``), the replay / gate facts and the
              latency. A pure side ledger: nothing reads it in the
              search path;
  columns.py — ``TableColumnCatalog``, the pg_catalog column-metadata
              layer behind the auto-load (one batched
              ``col_description`` query per result, TTL-cached per
              table, fail-soft);
  static_rules/ — the PYTHON-RULE corpora (``database/sql_views``
              templates as ns0 ``view`` entities + ``reads_from``
              edges derived by parsing: dir scan common module
              ``sources.py``, the ns0 rule ``sql_view_semantics.py``,
              the write boundary ``writer.py`` over
              ``database/sql_writes/`` action files). The service
              above READS; its graph (re)load runs the rules'
              READ-TIME FRESHNESS GATE (``freshness.py`` — corpus
              fingerprint ``llm.kg_rule_syncs`` vs the files'
              mtimes/count; stale → inline re-sync before the read,
              fail-soft), and the offline runner
              ``python -m llm_agents.kg.static_rules`` forces the same
              idempotent sync on demand.
"""
from llm_agents.kg.columns import (
    COLUMN_CACHE_TTL_SEC, TABLE_COLUMNS, TableColumnCatalog,
)
from llm_agents.kg.graph import (
    KgEdge, KgEntity, KnowledgeGraph, SearchTrace, SeedHit, SearchResult,
    WalkHit,
)
from llm_agents.kg.hotpaths import (
    PROMOTION_HIT_COUNT, HotpathCache, HotpathChain,
)
from llm_agents.kg.query_log import CachedChain, QueryLog
from llm_agents.kg.search_stats import KgSearchStatRow, SearchStatsLog
from llm_agents.kg.service import (
    KG_CACHE_TTL_SEC, NS_SCOPES, KgGateCandidate, KgGateVerdict,
    KgUnavailableError, KnowledgeGraphService,
    normalize_schema_scope, normalize_table_scope,
)

# The process-wide service — the TTL graph cache is only useful shared
# (every conversation of a run, the supervisor kit, the probes).
KNOWLEDGE_GRAPHS = KnowledgeGraphService()

__all__ = [
    "CachedChain", "COLUMN_CACHE_TTL_SEC", "HotpathCache", "HotpathChain",
    "KG_CACHE_TTL_SEC", "KNOWLEDGE_GRAPHS", "KgEdge", "KgEntity",
    "KgGateCandidate", "KgGateVerdict", "KgSearchStatRow",
    "KgUnavailableError", "KnowledgeGraph", "KnowledgeGraphService",
    "NS_SCOPES", "PROMOTION_HIT_COUNT", "SearchStatsLog", "SearchResult",
    "SearchTrace", "SeedHit", "QueryLog", "TABLE_COLUMNS",
    "TableColumnCatalog", "WalkHit",
    "normalize_schema_scope", "normalize_table_scope",
]
