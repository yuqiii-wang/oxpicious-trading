-- SQL write action — llm.kg_entities INSERT-or-refresh-mechanical (one
-- ns0 TABLE entity that a sql_views template references but no hand
-- seed has covered yet). Executed by llm_agents/kg/static_rules/
-- writer.py (StaticRulesWriter.apply) via asyncpg executemany BEFORE
-- the edge re-derivation, so the edge insert's by-name member
-- resolution finds it. The mechanical fallback row: description from
-- obj_description (the relation's own COMMENT ON TABLE — grounded,
-- never invented), empty tags (the entity is a WALK target; the
-- sql_views corpus carries the lexical seeds, the same division as
-- ns2). ON CONFLICT: the description refreshes ONLY while the row is
-- still mechanical (tags = '{}' — the hand seeds always carry tags),
-- so a comment-prose fix or cap change heals on the next run while a
-- hand-seeded row is never overridden by the rule.
--
-- Params (asyncpg $n, per row, in order):
--   $1 name        TEXT — schema-qualified relation name ('stats.foo')
--   $2 description TEXT — the relation's COMMENT ON TABLE prose,
--                  one-liner capped ('' when the relation carries none)
INSERT INTO llm.kg_entities (namespace, entity_type, name, description, tags)
VALUES (0, 'table', $1, $2, '{}')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description
    WHERE llm.kg_entities.tags = '{}'
