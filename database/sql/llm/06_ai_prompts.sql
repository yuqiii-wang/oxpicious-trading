-- ============================================================================
--  AI prompts — the deep-agent prompt registry (DDL only)
--  Table: llm.ai_prompts — the prompt rows the UI's Prompts picker
--  (data_viz AnalysisSummaryPage) lists and the deep agent
--  (llm_agents.deep_agents) applies to a run:
--
--    • the UI lists the active rows (GET /api/ai/prompts) and sends the
--      selected rows' prompt_key values in the generation request;
--    • the request's prompt_key list rides in the agent's structured data
--      (data.prompts); the agent FETCHES the matching instruction texts
--      itself through its read-only DB tools (db_run_query over this
--      table) and applies them — the instruction column is agent-facing
--      free text, the title/preview/note columns are UI-facing.
--
--  SEEDS live in the static dir database/sql/text/llm/static/ai_prompts/
--  (fresh-install source, applied after this DDL — one file per
--  prompt category):
--    • 01_deep_agent_prompts.sql — prompt_type 0 system + 3 internal,
--      generated from the Python constants by
--      temp_scripts/apply_deep_agent_prompts.py;
--    • 02_skills_db_query.sql / 03_skills_online_search.sql — the
--      skills' data-access / external-evidence conventions;
--    • 04_skills_biz_guides_general.sql + 05..08_skills_biz_guides_
--      {commons,signals,derivatives,composites}.sql — the biz_guide
--      guides, one per analysis scope / sub scope, grouped by the
--      UI's analysis category (general = the cross-cutting guides);
--    • 11_skills_backend_tech.sql — the backend study tech guides
--      (the study DB-table map, the sandbox study_kit usage);
--    • 12_skills_text_corpus.sql — the text-corpus audit guides (the
--      AI QA / news sampling-and-audit workflow, with the subagent
--      dive on suspicious samples);
--    • 09_plan_prompts.sql — prompt_type 2 plan;
--    • 10_tool_prompts.sql — prompt_type 4 tool, one row per
--      model-callable tool, generated from the deep-agent tool
--      classes (llm_agents/deep_agents/tools — the Tool subclasses'
--      DEFAULT_DESCRIPTION constants): the runner resolves the active
--      rows once per run and each tool kit overrides its tools' built-
--      in schema descriptions with them, fail-soft to the constants
--      (tools.base's override contract; tools/ is the single source —
--      regenerate the seed from the classes, never hand-write a
--      description here).
--
--  prompt_type is a closed vocabulary under the label-code contract
--  (static.label_dim domain prompt_type): 0 system = the run's standing
--  prompt, 1 skills = an approach instruction the UI picker lists, 2
--  plan = a generation-plan prompt (steps the agent lays out and
--  follows before writing), 3 internal = the deep agent's own machinery
--  prompt (planner system, step addendum, fork addenda; hidden from the
--  Skills picker, which lists only skills/plan labels), 4 tool = a tool
--  description (agent-facing schema text; hidden from the picker like
--  internal). prompt_key is
--  the stable join key (TEXT, not a label code): it names a behavior
--  preset, not a closed vocabulary domain — new prompts are plain
--  INSERTs (or edits) on this table, no label_dim mirror for the key
--  itself.
--
--  prompt_sub_type (deliberately TEXT like prompt_key — an open-ended
--  vocabulary that grows with new skill families, llm.* LLM layer, no
--  label_dim mirror): the skill's domain WITHIN its prompt_type, ''
--  for rows that need no split. Today's skills (prompt_type 1)
--  vocab: db_query = a data-access convention (how to query / rank),
--  online_search = an external-evidence convention, biz_guide = a
--  business-domain guide (one per analysis scope, e.g. MA, roof &
--  floor levels, and roof & floor AS signals are three guides),
--  backend_tech = a backend study tech guide (the DB table map, the
--  sandbox study_kit), text_corpus = a text-corpus audit guide (the
--  AI QA / news sampling-and-audit workflow). The UI nests the picker
--  rows into foldable sub_type sections.
--
--  version is a prompt's revision number WITHIN one (prompt_type, title):
--  seeded/manual rows start at 1, and every saved edit of the instruction
--  (PUT /api/ai/prompts/:prompt_key, the UI prompt view's Edit → Save)
--  bumps the SAME row's version by one — prompt_key never changes (it is
--  the stable join key riding the UI requests, the agent's fetches and
--  the persisted plan payloads). Manual multi-version rows per title (a
--  new key at version+1, the superseded row deactivated) stay possible;
--  the UNIQUE (prompt_type, title, version) triple keeps those distinct.
--
--  BOUND LABELS: bound_labels (TEXT[] default '{}', GIN-indexed as
--  idx_ai_prompts_bound_labels) binds a prompt to question vocabulary
--  — when the run's question contains one of the row's keywords
--  (case-insensitive substring), the deep agent force-loads that
--  prompt ALONGSIDE the UI-picked rows: the keyword-bound skill
--  applies without being picked. One keyword phrase per element
--  ('{}' = unbound); seeded for the scope-bound biz_guide guides
--  (commons / signals / derivatives / composites), '{}' for the
--  cross-cutting general guides.
--
--  OPTIONAL LABELS: optional_labels (TEXT[] default '{}', GIN-indexed
--  as idx_ai_prompts_optional_labels) is the prompt's free search
--  vocabulary — lowercase English/alias phrases in the
--  insight_page_docs.keywords style, complementing the bound labels'
--  Chinese terms. Labels make a prompt FINDABLE (array &&/@> filters
--  over the GIN index); bound_labels make it FORCE-LOAD.
--  Seeded for the scope-bound biz_guide guides, '{}' elsewhere.
--
--  DYNAMIC TEMPLATES: the runner (llm_agents/deep_agents/langgraph/runner.py)
--  renders the system prompt (the active prompt_type 0 row, else the
--  built-in default) and each picked plan prompt (prompt_type 2) as
--  templates before use — {{key}} placeholders fill from the run's
--  input args: sec_type / code / name (the classification-nav scope
--  pick; code carries the industry id on an industry pick) and
--  question (the focus instruction). Known keys always substitute
--  (empty string when the run carries no scope); unknown keys stay
--  literal so a typo is visible. Skills rows (prompt_type 1) are
--  fetched and applied by the agent itself — plain text today.
-- ============================================================================

CREATE TABLE IF NOT EXISTS llm.ai_prompts (
    prompt_key      TEXT        NOT NULL,  -- stable id the UI passes to the deep agent
    prompt_sub_type TEXT        NOT NULL DEFAULT '',  -- skill's domain within its type ('' = none)
    prompt_type     SMALLINT    NOT NULL,  -- label_dim prompt_type: 0 system | 1 skills | 2 plan | 3 internal
    title           TEXT        NOT NULL,  -- UI label (the picker's row title)
    version         SMALLINT    NOT NULL DEFAULT 1,  -- revision no. within (prompt_type, title); rework = +1 on a new key
    instruction     TEXT        NOT NULL,  -- agent-facing prompt instruction (fetched by prompt_key at run time)
    preview         TEXT        NOT NULL DEFAULT '',  -- one-sentence summary shown in the picker's row
    note            TEXT,                  -- optional UI hint under the title
    bound_labels    TEXT[]      NOT NULL DEFAULT '{}'::text[],  -- force-load keywords (GIN-indexed; contract on the COMMENT)
    optional_labels TEXT[]      NOT NULL DEFAULT '{}'::text[],  -- GIN-indexed search labels ('{}' = unlabeled)
    ordinal         SMALLINT    NOT NULL DEFAULT 0,  -- UI listing order
    is_active       BOOLEAN     NOT NULL DEFAULT true,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ai_prompts_pkey PRIMARY KEY (prompt_key, prompt_sub_type),
    CONSTRAINT ai_prompts_title_version_key UNIQUE (prompt_type, title, version)
);

CREATE INDEX IF NOT EXISTS idx_ai_prompts_optional_labels
    ON llm.ai_prompts USING GIN (optional_labels);

CREATE INDEX IF NOT EXISTS idx_ai_prompts_bound_labels
    ON llm.ai_prompts USING GIN (bound_labels);

COMMENT ON COLUMN llm.ai_prompts.prompt_sub_type IS
    'The skill''s domain WITHIN its prompt_type, '''' when the row needs '
    'no split. Deliberately TEXT (an open-ended vocabulary that grows '
    'with new skill families — llm.* LLM layer, no label_dim mirror). '
    'Part of the PK so one prompt_key may exist under several sub '
    'types. Today''s skills (prompt_type 1) vocab: db_query (a '
    'data-access convention) || online_search (an external-evidence '
    'convention) || biz_guide (a business-domain guide — one per '
    'analysis scope / sub scope, e.g. MA, roof & floor levels, and '
    'roof & floor AS signals are three guides) || backend_tech (a '
    'backend study tech guide — the DB table map, the sandbox '
    'study_kit) || text_corpus (a text-corpus audit guide — the AI QA '
    '/ news sampling-and-audit workflow). Today''s plan (prompt type '
    '2) vocab: goal (a study-scope directive — the four study-* rows; '
    'the picker renders the goal sub type as ONE mutually exclusive '
    'radio choice, none picked = the runner''s scope default) || '
    'orchestration (a plan-shape row the agent lays out and follows '
    'before writing — multi-pick).';

COMMENT ON COLUMN llm.ai_prompts.preview IS
    'One-sentence summary of the skill, shown as the picker row''s '
    'caption (falls back to note, then instruction).';

COMMENT ON COLUMN llm.ai_prompts.bound_labels IS
    'Force-load keywords, ONE distinctive phrase per element (a '
    'Chinese term or short token; long phrases dilute the match) — '
    'free-text question vocabulary, NOT the static.label_dim code '
    'labels. When the run''s question contains any element '
    '(case-insensitive substring), the deep agent force-loads the '
    'row''s instruction ALONGSIDE the user-picked prompts — the '
    'keyword-bound skill applies without being picked. ''{}'' = '
    'unbound. GIN-indexed (idx_ai_prompts_bound_labels) so the '
    'matcher prefilters candidate rows with bound_labels @> '
    'ARRAY[candidate keywords]. Seeded for the scope-bound biz_guide '
    'guides (commons / signals / derivatives / composites — one '
    'keyword set per analysis-scope lens); the cross-cutting general '
    'guides stay unbound.';

COMMENT ON COLUMN llm.ai_prompts.optional_labels IS
    'Optional search labels (TEXT[], open vocabulary — lowercase '
    'English/alias phrases in the insight_page_docs.keywords style), '
    'GIN-indexed (idx_ai_prompts_optional_labels) so prompt searches '
    'filter with the array overlap && / containment @> operators. '
    'Labels make a prompt FINDABLE in search; bound_labels '
    'FORCE-LOAD it on a question match — different jobs. Since '
    '2026-11-07 an optional-labeled row ALSO auto-loads when the '
    'KG-hotpath corroboration implicates it (planning/kg_prompts: the '
    'run''s question trigram-matches a PROMOTED llm.kg_hotpaths query '
    'and this row''s prompt_key names a chain entity, or its optional '
    'labels hit the chain''s bilingual entity vocabulary — the '
    '"hotpath" verdict loads like a bound force, capped by '
    'deep_agent_config prompt_hotpath_rows). ''{}'' = unlabeled. '
    'Seeded for the scope-bound biz_guide guides.';

COMMENT ON COLUMN llm.ai_prompts.version IS
    'Revision number within one (prompt_type, title): seeded/manual rows '
    'start at 1; every saved edit of the instruction bumps the same '
    'row''s version by one in place (prompt_key never changes). Manual '
    'multi-version rows per title (a new key at version+1, the '
    'superseded row is_active = false) stay possible; UNIQUE '
    '(prompt_type, title, version).';

COMMENT ON COLUMN llm.ai_prompts.prompt_type IS
    'label_dim domain prompt_type: 0 system (the run''s standing prompt) '
    '|| 1 skills (an approach instruction, the UI picker''s rows) '
    '|| 2 plan (a generation-plan prompt: steps the agent lays out and follows) '
    '|| 3 internal (the deep agent''s own machinery prompt — seeded from '
    'the Python constants, static/ai_prompts/01_deep_agent_prompts.sql; '
    'hidden from the picker) '
    '|| 4 tool (a model-callable tool''s description — one row per tool, '
    'prompt_key = the tool''s call name, seeded from the deep-agent tool '
    'classes by static/ai_prompts/10_tool_prompts.sql; the runner '
    'resolves the active rows once per run and the tool kits override '
    'the built-in schema descriptions with them, fail-soft to the '
    'constants; hidden from the picker).';
