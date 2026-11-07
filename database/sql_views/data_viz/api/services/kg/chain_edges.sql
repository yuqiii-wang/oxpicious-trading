-- AI monitor — ONE kg_search chain's edges (llm.kg_queries ⋈ llm.kg_edges):
-- the chain's logged edge ids resolved against the live hypergraph,
-- depth order restoring the chain's packing order. INNER JOIN = a
-- replay's own skip semantics — an edge the live graph no longer has
-- (purged / reseeded) drops out of the drawn chain instead of failing
-- the request. Serves GET /api/monitor/ai/kg-chain (the AI tab's
-- expandable chain rows).
-- args:
--   namespace: value int — the chain's kg_namespace code
--   chainId: value text — the chain's uuid hex
SELECT q.depth,
       e.edge_id,
       e.relation,
       e.sub_relation,
       e.entity_ids,
       e.weight
  FROM llm.kg_queries q
  JOIN llm.kg_edges e
    ON e.edge_id = q.edge_id AND e.namespace = q.namespace
 WHERE q.namespace = {{namespace}}
   AND q.chain_id = {{chainId}}
 ORDER BY q.depth
