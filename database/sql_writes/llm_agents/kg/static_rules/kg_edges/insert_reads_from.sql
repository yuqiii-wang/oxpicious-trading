-- SQL write action — llm.kg_edges INSERT (one ns0 reads_from edge).
-- Executed by llm_agents/kg/static_rules/writer.py
-- (StaticRulesWriter.apply) via asyncpg executemany after
-- delete_reads_from.sql, inside the same transaction. Head-first
-- hyperedge: entity_ids[0] is the sql_views template entity (the
-- SUBJECT — the reader), entity_ids[1] the base relation it reads.
-- Both members resolve BY NAME within namespace 0 (the seed-guard
-- contract — the writer's job, no FK can span the array); a pair
-- whose endpoint did not resolve inserts nothing, and the NOT EXISTS
-- guard on (relation, sub_relation, entity_ids) keeps a concurrent
-- seed application from doubling the row.
--
-- Params (asyncpg $n, per row, in order):
--   $1 view_name   TEXT — the template entity's name (slash path)
--   $2 table_name  TEXT — the referenced relation ('stats.index_basic_stats')
INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, 'reads_from', NULL,
       ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM llm.kg_entities a
JOIN llm.kg_entities b ON b.namespace = 0 AND b.name = $2
WHERE a.namespace = 0
  AND a.name = $1
  AND NOT EXISTS (
      SELECT 1 FROM llm.kg_edges x
      WHERE x.namespace = 0
        AND x.relation = 'reads_from'
        AND x.sub_relation IS NOT DISTINCT FROM NULL
        AND x.entity_ids = ARRAY[a.entity_id, b.entity_id]::BIGINT[])
