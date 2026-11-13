-- ============================================================================
--  KG static rules SYNC FINGERPRINTS + OWNERSHIP LEDGER (2026-11-09)
--
--  llm.kg_rule_syncs — one row per static rule
--  (llm_agents/kg/static_rules/): the corpus state its last sync
--  derived the KG rows from. The rule's corpus is a local directory
--  (ns0's database/sql_views templates; ns0's database/sql DDL
--  files' authored table banners); entities/edges have no updated_at of their own
--  (upserts keep created_at, edge ids churn on the re-derivation), so
--  THIS row is the only honest "the KG matches the corpus as of"
--  record.
--
--  The fingerprint is (file_count, max_mtime) — the corpus tree's file
--  count and its MAX(st_mtime) taken at sync time. max_mtime is
--  recorded from the SAME stat calls the comparisons later use, so the
--  read-time freshness gate (llm_agents/kg/service.py, on the graph
--  cache's reload path) compares mtime-to-mtime with no clock skew:
--
--      stale ⟺ row missing (bootstrap — the first read self-syncs)
--            OR corpus file count ≠ stored file_count (corpus files
--               added/deleted — the delete/insert diff)
--            OR corpus max(st_mtime) > stored max_mtime (a file
--               edited later than the sync)
--
--  On stale, the gate runs the rule's idempotent sync BEFORE the read
--  (prune stale rule-owned entities + upsert + re-derive the derived
--  edges + restamp THIS row — one transaction), then serves the fresh
--  graph. The stamp lands in the sync transaction itself: a crash
--  before the commit leaves no stamp, and the next read re-syncs.
--  Manual syncs (python -m llm_agents.kg.static_rules) stamp the same
--  way, so a just-run runner makes the gate a no-op until the corpus
--  moves again. Fail-soft: a gate error serves the stored graph and
--  logs — a KG tool result never breaks on the freshness layer.
--
--  llm.kg_rule_entities — the OWNERSHIP LEDGER: which entity names
--  each rule seeded. Needed by rules whose entity names cannot be
--  shape-distinguished from hand-seeded rows (the db_semantics_tables
--  rule's schema-qualified table names — the sql_views rule's slash
--  paths are self-identifying). The sync replaces the rule's ledger
--  rows in its transaction; the prune deletes ledger-listed entities
--  absent from the corpus TOGETHER with every edge touching them (no
--  ghost endpoints).
--
--  Re-appliable as-is (IF NOT EXISTS). Applied by
--  temp_scripts/apply_kg.py alongside the other llm/ DDL files.
-- ============================================================================

CREATE TABLE IF NOT EXISTS llm.kg_rule_syncs (
    rule_name   TEXT        NOT NULL,  -- the StaticRule.name ('sql_view_semantics')
    namespace   SMALLINT    NOT NULL,  -- domain 'kg_namespace': the KG subgraph the rule writes
    corpus_root TEXT        NOT NULL,  -- the scanned dir, repo-relative posix ('database/sql_views')
    file_count  INTEGER     NOT NULL,  -- corpus .sql file count as of the sync
    max_mtime   TIMESTAMPTZ NOT NULL,  -- corpus files' MAX(st_mtime) as of the sync (UTC)
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_kg_rule_syncs PRIMARY KEY (rule_name)
);


COMMENT ON TABLE llm.kg_rule_syncs IS
    'KG static rules corpus fingerprints — one row per rule: the (file_count, max_mtime) state its last sync derived the KG entities/edges from; the read-time freshness gate re-syncs when the corpus count differs or files are newer.';
