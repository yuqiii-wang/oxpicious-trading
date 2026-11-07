-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the OFTEN-USED SQL
--  JOIN PATHS (2026-11-05). One of the static/knowledge_graphs/ seed
--  files — DDL: 13_kg_hypergraph.sql; conventions: the kg-db-semantics
--  skill.
--
--  Scope: edges that answer "how do I JOIN these two tables" — the
--  recurring join conditions the repo's own SQL actually writes, each
--  grounded in a cited file (never invented):
--
--    relation 'joins_on', sub_relation = the JOIN KEY:
--      for_code_date  ON a.code = b.code AND a.date = b.date
--      for_code       ON cls.code = h.code (classification routing)
--
--  SYMMETRIC like similar_to — seed one direction, walkers traverse
--  either way (a join has no direction). Distinct from the existing
--  vocabulary: anchored_by says a table's GRAIN keys on an identity
--  table; joins_on names the fact↔fact (or fact↔reference) key two
--  PEER tables are combined on in practice.
--
--  Grounding (every edge below, by key):
--    for_code_date — analysis/mov_ave_spread/config/price_sources.py,
--      the shared fetch config of the whole mov-ave family:
--        PRICE_SOURCE['etf']:  stats.etf_basic_stats LEFT JOIN
--                              stats.etf_adjustment
--                              ON a.code = b.code AND a.date = b.date
--        AMT_SOURCE['etf']:    stats.etf_basic_stats LEFT JOIN
--                              stats.etf_liquidity_margin (same key)
--        AMT_SOURCE['stock']:  stats.stock_basic_stats LEFT JOIN
--                              stats.stock_liquidity_margin (same key)
--    for_code — the classification routing joins:
--      stats.sec_composition ⋈ stats.sec_classification
--        analysis/market_beta/attributions/sql_member_index.py and
--        sql_broad_market.py: JOIN stats.sec_classification cls
--        ON cls.code = h.code (holdings = sec_composition);
--      stats.index_basic_stats ⋈ stats.sec_classification
--        analysis/pair_pct_change_ratios/ratios.py:
--        ON sc.code = b.code;
--      stats.etf_liquidity_margin / stats.stock_liquidity_margin ⋈
--      stats.sec_classification — analysis/margins/fetch.py: the
--      margin sources (config.py SRC_TABLE_ETF / SRC_TABLE_STOCK)
--      routed to industries via sec_classification on code
--      ("Stock → industry_id via sec_classification").
--
--  Identity-grain joins (v_debt_daily on date, the *_daily views on
--  (date, code), options on (date, contract_code)) are already the
--  anchored_by edges' own contract — not duplicated here.
--
--  Idempotent — edges insert only when the same (namespace, relation,
--  sub_relation, entity_ids) shape is absent; entities are NOT
--  re-upserted (their rows belong to each scope's own seed file).
-- ============================================================================

INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    -- the mov-ave family's shared fetch joins (price_sources.py)
    ('stats.etf_basic_stats',       'stats.etf_adjustment',        'joins_on', 'for_code_date'),
    ('stats.etf_basic_stats',       'stats.etf_liquidity_margin',  'joins_on', 'for_code_date'),
    ('stats.stock_basic_stats',     'stats.stock_liquidity_margin','joins_on', 'for_code_date'),
    -- the classification routing joins (code → industry)
    ('stats.sec_composition',       'stats.sec_classification',    'joins_on', 'for_code'),
    ('stats.index_basic_stats',     'stats.sec_classification',    'joins_on', 'for_code'),
    ('stats.etf_liquidity_margin',  'stats.sec_classification',    'joins_on', 'for_code'),
    ('stats.stock_liquidity_margin','stats.sec_classification',    'joins_on', 'for_code')
) AS e(subject, object, relation, sub_relation)
JOIN llm.kg_entities a ON a.namespace = 0 AND a.name = e.subject
JOIN llm.kg_entities b ON b.namespace = 0 AND b.name = e.object
WHERE NOT EXISTS (
    SELECT 1 FROM llm.kg_edges x
    WHERE x.namespace = 0
      AND x.relation  = e.relation
      AND x.sub_relation IS NOT DISTINCT FROM e.sub_relation
      AND x.entity_ids = ARRAY[a.entity_id, b.entity_id]::BIGINT[]
);
