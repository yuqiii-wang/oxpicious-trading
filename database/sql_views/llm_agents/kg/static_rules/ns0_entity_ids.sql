-- KG static rules — the ns0 (DB semantics) entity inventory behind the
-- sql_views corpus rule: entity name per existing row, plus whether
-- the row is TAGGED (a hand seed — always authored with tags) or
-- mechanical (a previous static-rule insert, empty tags by design).
-- A link whose table entity is hand-seeded hits that stable id; a
-- referenced relation covered only mechanically is re-drafted every
-- run (the write action refreshes its description while still
-- mechanical — comment-prose fixes and cap changes heal; a hand seed
-- is never re-drafted, let alone overridden).
-- serves: llm_agents.kg.static_rules.sql_view_semantics.ViewSemanticsRule
-- args: none
SELECT name,
       (tags IS NOT NULL AND tags <> '{}') AS tagged
FROM llm.kg_entities
WHERE namespace = 0
ORDER BY name
