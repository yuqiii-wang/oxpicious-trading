-- KG static rules — the corpus fingerprint behind the read-time
-- freshness gate (static_rules/freshness.py): one rule's
-- llm.kg_rule_syncs row (its corpus file count and MAX mtime as of
-- the last sync). The gate stat-scans the corpus and compares — a
-- count diff, a newer file, or a MISSING row (bootstrap) means the KG
-- rows are derived from an older corpus and the rule re-syncs before
-- the graph is read.
-- serves: llm_agents.kg.static_rules.freshness.RuleFreshnessGate
-- args:
--   rule_name: value text — the StaticRule.name ('sql_view_semantics')
SELECT rule_name, file_count, max_mtime, synced_at
FROM llm.kg_rule_syncs
WHERE rule_name = {{rule_name}}::text
