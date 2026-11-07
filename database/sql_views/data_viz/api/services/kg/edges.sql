-- Knowledge Graph tab — ONE namespace's hyperedges (llm.kg_edges):
-- entity_ids is the ordered member list, head-first (entity_ids[0] =
-- subject); the service resolves the ids to names against the same
-- namespace's entities (a hyperedge never spans namespaces).
-- Serves GET /api/config/kg.
-- args:
--   namespace: value int — the kg_namespace code to list
SELECT edge_id, relation, sub_relation, entity_ids, weight
     FROM llm.kg_edges
     WHERE namespace = {{namespace}}
     ORDER BY edge_id
