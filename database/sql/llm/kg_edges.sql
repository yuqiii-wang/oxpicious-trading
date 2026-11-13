
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
