-- Knowledge Graph tab — ONE namespace's entity nodes (llm.kg_entities):
-- the canonical (namespace, entity_type, name) rows whose names the
-- edges' entity_ids resolve against. Namespace scopes the subgraph
-- (shipped codes 0 DB semantics | 1 prompts — the kg_namespace
-- vocabulary, deliberately not yet a label_dim domain).
-- Serves GET /api/config/kg.
-- args:
--   namespace: value int — the kg_namespace code to list
SELECT entity_id, name, entity_type, description, tags
     FROM llm.kg_entities
     WHERE namespace = {{namespace}}
     ORDER BY entity_type, name
