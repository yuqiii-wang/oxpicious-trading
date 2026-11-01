-- ============================================================================
--  AI prompts — the deep-agent prompt registry
--  Table: text.ai_prompts — the prompt rows the UI's Skills picker
--  (data_viz AnalysisSummaryPage) lists and the deep agent
--  (llm_agents.deep_agents) applies to a run:
--
--    • the UI lists the active rows (GET /api/ai/prompts) and sends the
--      selected rows' prompt_key values in the generation request;
--    • the request's prompt_key list rides in the agent's structured data
--      (data.prompts); the agent FETCHES the matching instruction texts
--      itself through its read-only DB tools (db_run_query over this
--      table) and applies them — the instruction column is agent-facing
--      free text, the title/note columns are UI-facing.
--
--  prompt_type is a closed vocabulary under the label-code contract
--  (static.label_dim domain prompt_type): 0 system = the run's standing
--  prompt, 1 skills = an approach instruction the UI picker lists, 2
--  plan = a generation-plan prompt (steps the agent lays out and
--  follows before writing), 3 internal = the deep agent's own machinery
--  prompt (planner system, step addendum, fork addenda — seeded from
--  the Python constants in 15_deep_agent_prompts.sql, resolved at run
--  start with the constants as fail-soft fallback; hidden from the
--  Skills picker, which lists only skills/plan labels). prompt_key is
--  the stable
--  join key (TEXT, not a label code): it names a behavior preset, not a
--  closed vocabulary domain — new prompts are plain INSERTs (or edits)
--  on this table, no label_dim mirror for the key itself.
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
--  DYNAMIC TEMPLATES: the runner (llm_agents/deep_agents/graph/runner.py)
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

CREATE TABLE IF NOT EXISTS text.ai_prompts (
    prompt_key   TEXT        PRIMARY KEY,  -- stable id the UI passes to the deep agent
    prompt_type  SMALLINT    NOT NULL,     -- label_dim prompt_type: 0 system | 1 skills | 2 plan
    title        TEXT        NOT NULL,     -- UI label (the picker's row title)
    version      SMALLINT    NOT NULL DEFAULT 1,  -- revision no. within (prompt_type, title); rework = +1 on a new key
    instruction  TEXT        NOT NULL,     -- agent-facing prompt instruction (fetched by prompt_key at run time)
    note         TEXT,                     -- optional UI hint under the title
    ordinal      SMALLINT    NOT NULL DEFAULT 0,  -- UI listing order
    is_active    BOOLEAN     NOT NULL DEFAULT true,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ai_prompts_title_version_key UNIQUE (prompt_type, title, version)
);

COMMENT ON COLUMN text.ai_prompts.version IS
    'Revision number within one (prompt_type, title): seeded/manual rows '
    'start at 1; every saved edit of the instruction bumps the same '
    'row''s version by one in place (prompt_key never changes). Manual '
    'multi-version rows per title (a new key at version+1, the '
    'superseded row is_active = false) stay possible; UNIQUE '
    '(prompt_type, title, version).';

COMMENT ON COLUMN text.ai_prompts.prompt_type IS
    'label_dim domain prompt_type: 0 system (the run''s standing prompt) '
    '|| 1 skills (an approach instruction, the UI picker''s rows) '
    '|| 2 plan (a generation-plan prompt: steps the agent lays out and follows) '
    '|| 3 internal (the deep agent''s own machinery prompt — seeded from '
    'the Python constants, 15_deep_agent_prompts.sql; hidden from the picker).';

-- Idempotent seed — re-running refreshes the built-in skills in place.
-- version stays at its seeded 1 and is deliberately NOT in the ON
-- CONFLICT SET: a seed re-run refreshes the texts but never resets a
-- manually bumped version.
INSERT INTO text.ai_prompts (prompt_key, prompt_type, title, version, instruction, note, ordinal) VALUES
    ('db-search',
     1,
     'DB search',
     1,
     'db-search: ground every claim in the data — inspect schemas '
     '(db_list_tables / db_describe_table) and run narrow aggregate '
     'queries (db_run_query) with the items'' metric db_keywords '
     'recipes before writing.',
     'Ground claims in actual DB values via the agent''s read-only tools.',
     1),
    ('rarity-ranking',
     1,
     'Rarity ranking',
     1,
     'rarity-ranking: weigh each metric against its own trailing '
     'history (percentile within the past ~10y) and call out '
     'top/bottom-tail readings explicitly.',
     'Flag metrics sitting in the tails of their own history.',
     2),
    ('cross-item-synthesis',
     1,
     'Cross-item synthesis',
     1,
     'cross-item-synthesis: cross-reference findings across the '
     'selected items — call out agreements, conflicts and the '
     'combined market picture in the closing section.',
     'Close with the combined picture, not per-item summaries only.',
     3),
    ('forked-evidence-search',
     1,
     'Forked evidence search',
     1,
     'forked-evidence-search: for every question that needs external '
     'evidence, FORK the search instead of querying inline — spawn '
     'parallel subagents, one per source lane: a CORPUS lane '
     '(text_search over the news / Q&A text schema, plus narrow '
     'db_run_query aggregates) for what the house text corpus already '
     'holds, and an ONLINE lane (web_search / web_summarize) for what '
     'is new or absent from the corpus. Read each fork''s final answer '
     'before concluding, attribute every fact to its lane (corpus vs '
     'web), and prefer the corpus lane for history and the online lane '
     'for freshness. A fork that needs deeper digging may spawn its '
     'own sub-sub search.',
     'Parallel forks: existing text corpus lane vs live web lane.',
     4)
ON CONFLICT (prompt_key) DO UPDATE
    SET prompt_type = EXCLUDED.prompt_type,
        title       = EXCLUDED.title,
        instruction = EXCLUDED.instruction,
        note        = EXCLUDED.note,
        ordinal     = EXCLUDED.ordinal,
        is_active   = true,
        updated_at  = now();

-- Plan-type seed (prompt_type 2) — the agent lays the plan out and
-- follows it before writing; the naive-verify plan re-checks research
-- claims AND technical stats.
INSERT INTO text.ai_prompts (prompt_key, prompt_type, title, version, instruction, note, ordinal) VALUES
    ('naive-verify-plan',
     2,
     'Naive verify plan',
     1,
     'naive-verify-plan: before writing, lay out a naive plan of checks '
     'and follow it step by step — (1) split the selected items into '
     'their research claims (the narrative readings each section will '
     'make) and their technical stats (indicator levels, recent changes, '
     'percentile rarities); (2) for every research claim, re-run the '
     'supporting aggregate query and confirm the cited number; (3) for '
     'every technical stat, confirm the reading against its own trailing '
     'history before using it; (4) state each step''s pass/fail in the '
     'working notes, drop or re-ground any claim that fails its check, '
     'and only then write the final document.',
     'Plan-then-verify: naive steps that re-check research claims and technical stats before writing.',
     10)
ON CONFLICT (prompt_key) DO UPDATE
    SET prompt_type = EXCLUDED.prompt_type,
        title       = EXCLUDED.title,
        instruction = EXCLUDED.instruction,
        note        = EXCLUDED.note,
        ordinal     = EXCLUDED.ordinal,
        is_active   = true,
        updated_at  = now();
