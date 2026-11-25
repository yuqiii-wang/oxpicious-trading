"""llm_agents.kg.graph — the DB-semantics knowledge-graph TRAVERSAL
SEARCH ENGINE (pure domain).

The graph itself lives in PostgreSQL (``llm.kg_entities`` /
``llm.kg_edges``, DDL ``database/sql/llm/kg_entities.sql`` +
``kg_edges.sql``): the
namespace-0 DB-semantics subgraph is seeded under
``database/sql/llm/static/knowledge_graphs/db_semantics/`` (ONE ENTITY
= ONE TABLE/VIEW, one edge = one table relationship); namespace 1 is
the PROMPT CATALOG (``prompts/01_prompt_catalog.sql`` — one entity per
cataloged ``llm.ai_prompts`` row, skills + plan prompts; similar_to
same-domain edges, alternates_with the mutually exclusive picks). The
engine below is namespace-blind: it searches whatever subgraph it is
handed. This module is the ONLINE half: instead of dumping the whole
schema or the whole catalog into the LLM payload, an agent searches
the graph by business meaning and receives the small relevant SUBGRAPH.

How a knowledge graph is traversed online (the retrieved-subgraph
pipeline the GraphRAG / HippoRAG / KGQA-subgraph-retrieval literature
converged on, distilled to what a 10²-node static schema graph needs):

  1. SEED RECOGNITION — lexical match of the query's terms against
     entity faces (name / bare table name / tags / description /
     entity_type), BILINGUAL by construction: tags carry the trader's
     Chinese term beside the English one ({指数, ohlcv, K线, 动量}).
     CJK text is matched by CHARACTER BIGRAMS (segmenter-free recall:
     "热门股动量" shares 热门/门股/动量 with the tag 热门股 and the tag
     动量), latin text by whole word tokens; each token is weighted by
     IDF so 指数 (in half the descriptions) cannot outvote rsi. Since
     2026-11-23 a token with NO exact bucket seeds through the FUZZY
     LANE (``KnowledgeGraph.plural_stem`` / ``fuzzy_match``): an
     English plural strips to an exact index token, a close misspelling
     (character-trigram similarity ≥ ``vocabulary.FUZZY_SIMILARITY_MIN``)
     maps onto its nearest vocabulary word — strictly BELOW the exact
     hit of the same face, and marked ``token[face≈matched]`` in the
     provenance line; latin words of 4+ characters only.
  2. SPREADING ACTIVATION — the seeds' energy propagates along edges
     with a per-hop decay and per-(relation, direction) factors
     (``similar_to`` is the strongest symmetric family hint; a DERIVED
     table walking INTO its source ranks the source, a source walking
     outward finds its derivations; ``anchored_by`` INTO the identity
     table is damped — identity tables are hubs). The out-energy of a
     node SPLITS across its incident edges by factor (fan-out
     normalized, energy preserving — a hub cannot flood the graph),
     and an edge whose ``sub_relation`` agrees with the query's tokens
     (for_close, for_pe …) carries ×1.6. Activation ACCUMULATES: a
     node reached by two independent paths outranks a single-hop
     acquaintance — the coherence signal spreading activation is for.
     The per-hop frontier is beam-bounded.
  3. RANK + PACK — seeds keep their lexical score; propagated nodes
     score their activation on the WALK's own scale (the strongest
     propagated row = ``ACT_BLEND``), and seeds structurally PRECEDE
     them (connectivity alone never outranks a table that NAMES the
     ask). Each class has a relevance floor relative to the question —
     the seed tail a top seed dwarfs drops out; propagated rows are
     floored at ``_WALK_FLOOR`` of the overall top, so the more
     specifically the ask names its tables the fewer propagated rows
     survive (``vocabulary``). The top-N nodes and the edges AMONG them
     (the join/derivation paths the agent actually needs) are rendered
     as one bounded text block.

Submodules: ``values`` (the frozen value types), ``vocabulary``
(the shared match vocabulary + every tuning constant), ``engine``
(the :class:`KnowledgeGraph` traversal), ``rendering`` (the bounded
text blocks) — all re-exported here so ``from llm_agents.kg.graph
import …`` and ``from llm_agents.kg import graph`` keep one face.

Pure domain: no DB, no provider, no LangGraph import — stub-testable
like ``planning.plan_json`` (see ``temp_scripts/test_kg_search.py``).
The DB load lives in :mod:`.service` (the TTL-cached
``KnowledgeGraphService``); the model-facing tools in
``llm_agents.deep_agents.tools.kg_tools.kit``.
"""
from llm_agents.kg.graph.engine import KnowledgeGraph
from llm_agents.kg.graph.rendering import (
    DETAIL_FULL, DETAIL_LEAN, DETAIL_TIERS, NODE_MAX_CAP, NODE_MAX_DEFAULT,
    normalize_detail, render_expand, render_replay, render_search,
)
from llm_agents.kg.graph.values import (
    KgEdge, KgEntity, ScoredNode, SearchResult, SearchTrace, SeedHit,
    WalkHit,
)
from llm_agents.kg.graph.vocabulary import (
    ACT_BLEND, FACE_DESC, FACE_NAME, FACE_TAG, FACE_TYPE, HOP_DECAY,
    SUBRELATION_BOOST, WALK_BEAM, edge_factor, extract_tokens, normalize,
)

__all__ = [
    "ACT_BLEND", "DETAIL_FULL", "DETAIL_LEAN", "DETAIL_TIERS",
    "FACE_DESC", "FACE_NAME", "FACE_TAG", "FACE_TYPE",
    "HOP_DECAY", "KgEdge", "KgEntity", "KnowledgeGraph", "NODE_MAX_CAP",
    "NODE_MAX_DEFAULT", "ScoredNode", "SearchResult", "SearchTrace",
    "SeedHit", "SUBRELATION_BOOST", "WalkHit", "WALK_BEAM", "edge_factor",
    "extract_tokens", "normalize", "normalize_detail", "render_expand",
    "render_replay", "render_search",
]
