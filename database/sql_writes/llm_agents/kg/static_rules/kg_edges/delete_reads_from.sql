-- SQL write action — llm.kg_edges DELETE (every ns0 reads_from edge).
-- Executed by llm_agents/kg/static_rules/writer.py
-- (StaticRulesWriter.apply) at the head of the edge re-derivation: the
-- view→table edge set is FULLY DERIVED from the scanned
-- database/sql_views corpus (parsed by the rule on every run), so the
-- sync replaces it wholesale instead of diffing. Only the rule's own
-- relation is touched — every hand-seeded edge (computed_from,
-- joins_on, …) is untouched.
--
-- Params: none
DELETE FROM llm.kg_edges
WHERE namespace = 0 AND relation = 'reads_from'
