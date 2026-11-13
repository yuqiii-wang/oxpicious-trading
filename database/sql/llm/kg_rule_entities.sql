
CREATE TABLE IF NOT EXISTS llm.kg_rule_entities (
    rule_name   TEXT NOT NULL,  -- the StaticRule.name ('db_semantics_tables')
    entity_name TEXT NOT NULL,  -- the entity name the rule seeded ('stats.index_basic_stats')
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_kg_rule_entities PRIMARY KEY (rule_name, entity_name)
);

COMMENT ON TABLE llm.kg_rule_entities IS
    'KG static rules ownership ledger — the entity names each rule seeded; the prune deletes ledger-listed entities absent from the rule corpus together with every edge touching them.';
