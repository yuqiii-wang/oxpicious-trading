-- ============================================================================
--  Deep-agent LANGGRAPH templates — reusable graph definitions, typed by
--  which of the run's three chained LangGraphs they drive.
--  Table: llm.deep_agent_langgraph_templates
--  (renamed from deep_agent_plan_templates 2026-11-06, the three-graph
--  refactor: one run = plan → custom → result, three threads chained by
--  llm.deep_agent_states.super_thread_id)
--
--  `type` (static.label_dim domain 'langgraph_type'):
--    0 plan   — a FINISHED run's plan JSON saved as a reusable
--               placeholder template so a later generate can SKIP the
--               LLM planning session entirely (the Thread Graph Save
--               button's rows; the feature this table was born as).
--    1 custom — reserved: a saved CUSTOM-graph step set (the on-demand
--               graph the planner builds is type custom at runtime; a
--               row of this type would template it directly).
--    2 result — the RESULT graph's fixed definition: the four review
--               nodes (return_estimate / risk_analysis / optimistic_
--               review / pessimistic_review) as one parallel wave. SEEDED
--               below ('default-result-wave') — the runner builds the
--               result graph from the row, so the review goals are
--               DB-editable without a code change.
--
--  PLAN templates (type 0): the UI's Thread Graph Save button (settled
--  runs only) copies the plan graph's kind 'plan' context payload here
--  with the run-specific VARIABLE parts replaced by {{key}} placeholders
--  — the SAME vocabulary the prompt templates use (planner.template_args:
--    {{question}} the run's focus question, {{code}} / {{name}} /
--    {{sec_type}} the classification-nav scope pick
--  longest-value-first, so a subject mentioned inside the question
--  placeholders too). The bridge's save (data_viz/api/services/
--  ai-plan-template.service.ts) derives the original values from the
--  plan payload's 'args' block (runner-stamped at create_thread since
--  the templates feature) falling back to llm.deep_agent_identities
--  (older runs: code/name only — the question is not recoverable and
--  stays literal).
--
--  REUSE: a generate carrying the template id (POST /api/ai/summary
--  planTemplateId → the daemon's /run plan_template_id → run_thread →
--  the plan session's template lane in planning/templates.py) fetches
--  the row, renders every {{key}} with the NEW run's input args
--  (planner.render_template — known keys always substitute, empty when
--  the run carries none) and persists the steps one-to-one as pending
--  state rows of the CUSTOM graph's thread: planning finishes
--  immediately, ZERO planning LLM calls. Fail-soft as everywhere in the
--  harness — a missing/broken template falls back to the normal LLM
--  proposal session. A saved plan is the BUSINESS MIDDLE only: the
--  four reviews and the final synthesis belong to the run's separate
--  RESULT graph (a plan never saves a closing section for them).
--
--  thread_id is provenance ONLY — no FK: a template deliberately
--  OUTLIVES its source run (deleting a summary run keeps the templates
--  saved from it). name is UNIQUE — saving under an existing name
--  overwrites it (the upsert refresh path); the seeded result template
--  reserves its name so a user save can never collide with it.
--
--  Applied after 07_deep_agent_states.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS llm.deep_agent_langgraph_templates (
    template_id  BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         TEXT        NOT NULL UNIQUE,   -- the picker's label; saving under an existing name overwrites
    type         SMALLINT    NOT NULL DEFAULT 0,
                 -- static.label_dim 'langgraph_type': 0 plan (a saved
                 -- plan template — reuse skips the planning session),
                 -- 1 custom (a saved custom-graph step set — reserved),
                 -- 2 result (the result graph's four-review-wave
                 -- definition — seeded, DB-editable review goals)
    thread_id    TEXT,                          -- the source run (uuid hex, provenance only — no FK: the template outlives the thread)
    args         JSONB       NOT NULL DEFAULT '{}',
                 -- the SOURCE run's template args ({question, code, name,
                 -- sec_type} — planner.template_args; older runs: the
                 -- identities fallback subset). What the placeholders
                 -- replaced — provenance, never rendered.
    plan_json    JSONB       NOT NULL,          -- the templated graph payload {"steps": [...]} — {{key}} placeholders where the run's values stood
    n_steps      SMALLINT    NOT NULL,          -- step count (the picker's caption)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE llm.deep_agent_langgraph_templates IS
    'Deep-agent LangGraph templates: reusable graph definitions typed by '
    'the run''s three chained graphs (plan / custom / result — '
    'static.label_dim domain langgraph_type). Type plan rows are a '
    'finished run''s plan JSON saved as a reusable {{key}} placeholder '
    'template ({{question}} / {{code}} / {{name}} / {{sec_type}} — the '
    'planner template_args vocabulary; saved by the Thread Graph''s Save '
    'button, reused by a generate carrying the template id — planning '
    'finishes with zero LLM calls). Type result rows define the RESULT '
    'graph''s fixed four-review wave (seeded as default-result-wave — '
    'the runner builds the result graph from the row, so the review '
    'goals are DB-editable). See llm_agents/deep_agents/planning/'
    'templates.py.';

COMMENT ON COLUMN llm.deep_agent_langgraph_templates.name IS
    'The template''s display name (the generate form''s picker label). '
    'UNIQUE — saving under an existing name overwrites that row (upsert); '
    'the seeded result template''s name is reserved.';

COMMENT ON COLUMN llm.deep_agent_langgraph_templates.type IS
    'Which of the run''s three chained LangGraphs the template drives — '
    'static.label_dim domain ''langgraph_type'': 0 plan (a saved plan '
    'template — reuse skips the planning session), 1 custom (a saved '
    'custom-graph step set — reserved vocabulary), 2 result (the result '
    'graph''s four-review-wave definition — the seeded default-result-'
    'wave row; plan_template_id loads never read it).';

COMMENT ON COLUMN llm.deep_agent_langgraph_templates.thread_id IS
    'The source run''s thread id (uuid hex) — provenance only, NO FK: a '
    'template deliberately outlives its source run (the summary-history '
    'delete may purge the thread while templates saved from it stay).';

COMMENT ON COLUMN llm.deep_agent_langgraph_templates.args IS
    'The SOURCE run''s template args ({question, code, name, sec_type} — '
    'planner.template_args; runs older than the templates feature carry '
    'the identities-fallback subset {sec_type, code, name}). The values '
    'the placeholders replaced — provenance for what varied, never '
    'rendered at reuse time.';

COMMENT ON COLUMN llm.deep_agent_langgraph_templates.plan_json IS
    'The templated graph payload {"steps": [{"node", "title", "notes", '
    '"delegate": {"goal"}, "parallel"}]} — the graph''s plan with its '
    'variable parts replaced by {{key}} placeholders (longest-value-first '
    'substitution at save time). Reuse renders every placeholder with the '
    'new run''s args (planner.render_template) and persists the steps as '
    'pending llm.deep_agent_states rows; the seeded result template is '
    'the fixed wave definition the result graph is built from.';

COMMENT ON COLUMN llm.deep_agent_langgraph_templates.n_steps IS
    'The templated graph''s step count (the picker''s caption).';

-- ---------------------------------------------------------------------------
--  SEED — the RESULT graph's default wave definition (type 2). The four
--  fixed review seats of planning/plan_json.py::REVIEW_WAVE_NODES as one
--  parallel-marked wave; the result graph's builder reads this row
--  (templates.fetch_result_template) and falls back to the built-in
--  constants fail-soft. Idempotent: the reserved name never overwrites.
-- ---------------------------------------------------------------------------
INSERT INTO llm.deep_agent_langgraph_templates
    (name, type, plan_json, n_steps)
VALUES
    ('default-result-wave', 2,
     '{"steps": ['
     '  {"node": "return_estimate",'
     '   "title": "Expected return over the next 1-3 months: direction, '
     'indicative range and confidence, weighed from the findings so far",'
     '   "parallel": true},'
     '  {"node": "risk_analysis",'
     '   "title": "Material risks to the view ranked by probability '
     'times impact, each with its trigger and the evidence that signals '
     'it",'
     '   "parallel": true},'
     '  {"node": "optimistic_review",'
     '   "title": "Strongest bull case the accumulated evidence '
     'supports: steelman the upside, its conditions and catalysts",'
     '   "parallel": true},'
     '  {"node": "pessimistic_review",'
     '   "title": "Strongest bear case against the view: stress the '
     'weakest findings, conflicts and failure modes with their triggers",'
     '   "parallel": true}'
     ']}'::jsonb,
     4)
ON CONFLICT (name) DO NOTHING;
