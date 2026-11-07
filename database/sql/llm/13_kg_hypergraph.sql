-- ============================================================================
--  Knowledge hypergraph — MINIMALIST SETUP (2026-11-04)
--  Tables: llm.kg_entities — one row per entity node (namespace + name + open type + tags)
--          llm.kg_edges    — one row per hyperedge (namespace + relation + sub_relation + member list)
--
--  namespace (SMALLINT, label_dim domain kg_namespace) scopes the graph:
--  one namespace = one source corpus's subgraph. Shipped codes:
--  0 DB semantics (one entity per table/view — the database describes
--  itself; seeds static/knowledge_graphs/db_semantics/) | 1 prompts
--  (one entity per cataloged llm.ai_prompts row, types 1 skills +
--  2 plan — the prompt catalog describes itself; seeds
--  static/knowledge_graphs/prompts/). Entities are unique WITHIN a
--  namespace only: the same surface name in two corpora is two nodes.
--
--  label-code contract status: kg_namespace is deliberately NOT yet
--  registered in label_dim (no writer, no data) — the code spellings in
--  the column comments below are the only vocabulary record for now
--  (provisionally mirrored on search_table's corpus order). Registering
--  the domain in all three mirrors (static/label_dim/07_text_llm.sql
--  INSERT + _common/labels.py + data_viz/api/services/label-codes.ts)
--  is part of shipping the first KG writer.
--
--  A hyperedge connects N entities in ONE row: entity_ids is the ordered
--  member list, head-first — entity_ids[0] is the head/subject, the rest
--  are the objects (a plain graph triple is just cardinality 2). No FK can
--  reach into an array, so member existence is the WRITER's job (resolve
--  names via uq_kg_entities_ns_type_name, then insert the ids — same
--  namespace only).
--
--  entity_type / relation are deliberately TEXT (not SMALLINT label codes):
--  the vocabulary is open-ended — LLM extraction coins new types/relations
--  as the corpus grows — and open-ended vocabularies keep TEXT per the
--  label_dim contract (01_label_dim.sql "NOT converted" list).
--
--  Upgrade (2026-11-04): namespace + weight bound — installs that applied
--  the first version of this file need the one-time ALTER from a
--  temp_scripts run (ADD COLUMN namespace SMALLINT NOT NULL on both
--  tables, swap the entity unique constraint, rebuild the name index,
--  ADD CONSTRAINT ck_kg_edges_weight on llm.kg_edges); this CREATE is
--  already canonical for fresh installs.
--
--  No writer yet — schema-first setup. Re-appliable as-is (IF NOT EXISTS).
-- ============================================================================

CREATE TABLE IF NOT EXISTS llm.kg_entities (
    entity_id   BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    namespace   SMALLINT    NOT NULL,  -- domain 'kg_namespace': 0 relational DB semantics
    name        TEXT        NOT NULL,  -- canonical surface form, e.g. 上证指数 / 半导体
    entity_type TEXT        NOT NULL,  -- open vocabulary: index | industry | stock | concept | macro | ...
    description TEXT,                  -- optional one-liner the extractor attached
    tags        TEXT[],                -- open-vocabulary filter tags, e.g. {index, bars} — GIN-indexed
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_kg_entities_ns_type_name UNIQUE (namespace, entity_type, name)
);

-- Namespace-scoped name lookups (alias/mention resolution at extraction
-- time — resolves within the subgraph it is building, never across).
CREATE INDEX IF NOT EXISTS ix_kg_entities_ns_name ON llm.kg_entities (namespace, name);

-- Tag containment lookups (WHERE tags @> ARRAY['index']) — same array
-- access pattern as the edges' GIN below.
CREATE INDEX IF NOT EXISTS ix_kg_entities_tags_gin ON llm.kg_entities USING gin (tags);

CREATE TABLE IF NOT EXISTS llm.kg_edges (
    edge_id     BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    namespace   SMALLINT    NOT NULL,  -- domain 'kg_namespace' (same codes as kg_entities) — the edge's subgraph
    relation    TEXT        NOT NULL,  -- open vocabulary: belongs_to | drives | competes_with | ...
    sub_relation TEXT,                 -- narrowed semantics of the relation (NULL = bare verb) — see column comment
    entity_ids  BIGINT[]    NOT NULL,  -- ordered members, head-first (entity_ids[0] = subject)
    weight      REAL,                  -- optional signed strength in [-1.0, 1.0] (extractor's call; NULL = unrated)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_kg_edges_members CHECK (cardinality(entity_ids) >= 2),
    CONSTRAINT ck_kg_edges_weight CHECK (weight BETWEEN -1.0 AND 1.0)
);

-- "All edges touching entity X" — the only access path a KG walker needs.
CREATE INDEX IF NOT EXISTS ix_kg_edges_entity_ids_gin
    ON llm.kg_edges USING gin (entity_ids);

COMMENT ON TABLE llm.kg_entities IS
    'Knowledge-hypergraph entity nodes (minimalist setup, no writer yet): '
    'one row per canonical (namespace, entity_type, name) — namespace '
    'scopes the source corpus''s subgraph (domain kg_namespace). Types '
    'are an open vocabulary in TEXT by design — LLM extraction coins new '
    'ones; do not convert to SMALLINT label codes.';

COMMENT ON COLUMN llm.kg_entities.namespace IS
    'Source-corpus subgraph scope — planned label_dim domain '
    'kg_namespace, NOT yet registered (no writer/no LLM extraction '
    'pipeline yet; the codes live in this file''s header until '
    'registration ships the three mirrors): 0 DB semantics (one '
    'entity per table/view) | 1 prompts (one entity per cataloged '
    'llm.ai_prompts row, types 1+2). Uniqueness is per namespace.';

COMMENT ON COLUMN llm.kg_entities.tags IS
    'Open-vocabulary filter tags (TEXT[], GIN-indexed for tags @> '
    'containment lookups) naming the entity''s subject domain and role, '
    'e.g. {index, bars}. Deliberately TEXT, not SMALLINT label codes — '
    'the vocabulary grows without code changes (label_dim "NOT '
    'converted" rule). NULL = untagged.';

COMMENT ON TABLE llm.kg_edges IS
    'Knowledge-hypergraph hyperedges (minimalist setup, no writer yet): '
    'one row per (namespace, relation[, sub_relation], ordered member '
    'list). entity_ids is head-first (entity_ids[0] = subject); members '
    'are resolved to llm.kg_entities.entity_id by the WRITER — no FK can '
    'span the array, and members always belong to the edge''s namespace. '
    'Relations are an open TEXT vocabulary by design.';

COMMENT ON COLUMN llm.kg_edges.sub_relation IS
    'Narrowed semantics of the relation — WHAT the edge specifically says '
    'beyond its verb. relation keeps the plain verb; sub_relation reads '
    'as a PREPOSITIONAL PHRASE (for_… / about_…): (anchored_by, '
    'for_code_date) = the fact table anchors on the identity''s (code, '
    'date) grain; (computed_from, for_close) = the derivation is for the '
    'source''s closes; (similar_to, about_momentum) = both tables are '
    'momentum gauges. Open TEXT vocabulary like relation itself; NULL = '
    'the bare verb, not yet narrowed.';

COMMENT ON COLUMN llm.kg_edges.namespace IS
    'The edge''s subgraph — same codes as llm.kg_entities.namespace (a '
    'hyperedge never spans namespaces); planned label_dim domain '
    'kg_namespace, not yet registered.';

COMMENT ON COLUMN llm.kg_edges.entity_ids IS
    'Ordered member entity ids, head-first: entity_ids[0] is the head/'
    'subject, the rest are objects — cardinality 2 degenerates to a plain '
    'graph edge (enforced >= 2). Uniqueness/direction semantics beyond '
    'order are left to the (future) writer.';

COMMENT ON COLUMN llm.kg_edges.weight IS
    'Optional signed edge strength, bounded to [-1.0, 1.0] '
    '(ck_kg_edges_weight): positive = reinforcing/positive association, '
    'negative = inverse/negative association, NULL = unrated. Beyond the '
    'bound, the scale semantics are the (future) writer''s to define.';
