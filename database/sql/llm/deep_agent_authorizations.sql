-- ============================================================================
--  Deep-agent authorizations — per-run DB-table read scope (the "temp user")
--  Table: llm.deep_agent_authorizations
--
--  description: A deep-agent run's DB-table authorization records — the per-run "temp user" row (init table scope from the UI picks) plus per-node extension rows for study-focused sub-agents; the scope gate reads them to decide which tables an agent's SQL may touch.
--  tags: agent authorization, 智能体授权, table scope, 表范围, permission, 权限, deep agent, 深度智能体
--
--  TWO AREAS OF WORK, TWO ROW KINDS:
--    (1) THE THREAD TEMP USER — one row per super-thread run with
--        node_id NULL, written at RUN START by the harness from the
--        request's init scope (the UI's ticked tables / forced schemas;
--        daemon/CLI runs without picks default to the standing schema
--        allowlist). Every SQL the run's agents issue is checked against
--        this row's permission.
--    (2) NODE EXTENSION ROWS — sub-nodes INHERIT their parent's scope by
--        construction (no row); only a node EXPLICITLY given a study
--        focus gets a row keyed (super_thread_id, node_id), its
--        permission the UNION of the parent's scope with the tables the
--        knowledge graph locates for that focus (extend, never narrow).
--        node_id = llm.deep_agent_states.state_id (the node's state row).
--
--  permission JSONB (one shape, both row kinds):
--    {"tables":        ["analysis.mov_ave_rsi", ...],   -- schema-qualified read allowlist
--     "schemas":       ["analysis", "stats", ...],      -- schema allowlist
--     "added_tables":  ["stats.v_index_daily"],         -- node rows: the KG-located delta
--     "focus":         "量价背离 study",                  -- node rows: the explicit study focus
--     "source":        "ui_init" | "kg_extension"}
--  allowed(table) <=> table IN tables OR schema(table) IN schemas.
--  Node rows materialize the effective union at write time, so scope
--  resolution is ONE row read (node row first, thread row fallback).
--
--  KEYING NOTE: the design key is (super_thread_id, node_id) with node_id
--  nullable — PG cannot put a nullable column in a PRIMARY KEY, so the
--  surrogate auth_id is the PK and two PARTIAL UNIQUE INDEXES render the
--  intent: one thread row per super_thread_id (node_id IS NULL) and one
--  row per (super_thread_id, node_id) otherwise. super_thread_id is the
--  CHAIN id (deep_agent_states.super_thread_id; NULL there means a
--  legacy single-thread run read as its own chain via COALESCE(
--  super_thread_id, thread_id)). No FK: chain rows are deleted with the
--  run, never referenced (the house super_thread_id pattern).
--
--  ENFORCEMENT: the harness CODE GATE (llm_agents/deep_agents/core/
--  authorization) checks every agent SQL lane — db_run_query /
--  db_describe_table / db_list_tables / text_search MCP calls and the
--  stats study tools — against the resolved permission BEFORE execution,
--  rejecting out-of-scope reads with a kg_search remedy. The py-sandbox
--  lane keeps the shared readonly_analytics role this round (documented
--  gap; a per-run PG role is the follow-up hardening layer).
--
--  Purge: with the run — delete_for_thread (chain purge) +
--  09_deep_agent_runs_prune.sql.
--
--  WRITER: llm_agents/deep_agents/core/authorization (AuthorizationStore)
--  — fail-soft, never on the request path of an LLM call.
-- ============================================================================

CREATE TABLE IF NOT EXISTS llm.deep_agent_authorizations (
    auth_id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    super_thread_id TEXT        NOT NULL,  -- the CHAIN id (the run's root thread id)
    node_id         BIGINT,                -- llm.deep_agent_states.state_id — NULL = the thread temp-user row
    permission      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ONE thread temp-user row per run (the design key's NULL leg — PG PKs
-- cannot be nullable, so the partial unique index IS the constraint).
CREATE UNIQUE INDEX IF NOT EXISTS ux_deep_agent_auth_thread
    ON llm.deep_agent_authorizations (super_thread_id)
    WHERE node_id IS NULL;

-- ONE extension row per explicitly study-focused node.
CREATE UNIQUE INDEX IF NOT EXISTS ux_deep_agent_auth_node
    ON llm.deep_agent_authorizations (super_thread_id, node_id)
    WHERE node_id IS NOT NULL;

COMMENT ON TABLE llm.deep_agent_authorizations IS
    'Deep-agent DB-table authorizations: one THREAD TEMP-USER row per '
    'super-thread run (node_id NULL, the init scope the UI picked — the '
    'tick tables / forced schemas, default the standing schema allowlist) '
    'plus one EXTENSION row per explicitly study-focused node (node_id = '
    'deep_agent_states.state_id; permission = parent scope UNION the KG-'
    'located tables for the focus — extend, never narrow). Sub-nodes '
    'without a row inherit their parent''s scope by construction. The '
    'harness scope gate (core/authorization) resolves a node''s effective '
    'permission here (node row first, thread row fallback) and checks '
    'every agent SQL lane against it BEFORE execution. Written fail-soft '
    'at run start / spawn time; purged with the run.';

COMMENT ON COLUMN llm.deep_agent_authorizations.super_thread_id IS
    'The run''s CHAIN id (deep_agent_states.super_thread_id — the root '
    'plan thread''s own id; legacy single-thread runs read as their own '
    'chain via COALESCE(super_thread_id, thread_id)). No FK: chain rows '
    'are deleted with the run, never referenced.';

COMMENT ON COLUMN llm.deep_agent_authorizations.node_id IS
    'The authorized node''s llm.deep_agent_states.state_id. NULL = the '
    'run''s THREAD TEMP-USER row (the init scope every un-rowed node '
    'falls back to). NOT NULL = an EXPLICIT EXTENSION row for one node: '
    'the spawn carried a study focus, the harness KG-located the related '
    'tables and unioned them onto the parent scope. Design key is '
    '(super_thread_id, node_id) with node_id nullable — PG PKs cannot be '
    'nullable, so the partial unique indexes ux_deep_agent_auth_thread / '
    'ux_deep_agent_auth_node render it.';

COMMENT ON COLUMN llm.deep_agent_authorizations.permission IS
    'The effective read scope as ONE JSON object: {tables: schema-'
    'qualified allowlist, schemas: schema allowlist, added_tables: the '
    'node row''s KG-located delta, focus: the node row''s study focus, '
    'source: ui_init | kg_extension}. allowed(table) <=> table IN tables '
    'OR schema(table) IN schemas. Node rows materialize the parent UNION '
    'at write time, so resolution is one row read.';
