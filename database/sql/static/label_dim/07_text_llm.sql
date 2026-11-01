-- ============================================================================
--  static.label_dim seed — TEXT / LLM-LAYER vocabularies
--
--  The text-schema knowledge-base closed vocabularies. Owning tables:
--  text.llm_qa_by_research (research_logic — the dominant investing
--  logic a research-logic Q&A row classifies), text.ai_prompts
--  (prompt_type — the prompt row's role in a deep-agent run) and
--  text.deep_agent_contexts (agent_context_kind — which payload a
--  context row carries, incl. the runner's hardcoded final-result
--  node output). First domain of the schema's LLM-layer enums converted
--  to the label-code contract (2026-10-27); further text.* conversions
--  register here.
-- ============================================================================

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- research_logic: text.llm_qa_by_research.logic_label — the dominant
    -- investing logic of the row's underlying industry/security (what the
    -- answer's intro/return/risks decomposition explains). 99 other = the
    -- fallback (the logic did not fit / the model could not classify)
    ('research_logic',  0, 'growth',    0),
    ('research_logic',  1, 'value',     1),
    ('research_logic',  2, 'dividend',  2),
    ('research_logic',  3, 'commodity', 3),
    ('research_logic',  4, 'cyclical',  4),
    ('research_logic',  5, 'defensive', 5),
    ('research_logic',  6, 'quality',   6),
    ('research_logic',  7, 'theme',     7),
    ('research_logic', 99, 'other',    99),
    -- prompt_type: text.ai_prompts.prompt_type — the row's role in a
    -- deep-agent run: 0 system = the run's standing prompt, 1 skills =
    -- an approach instruction (the UI Skills picker's rows), 2 plan = a
    -- generation-plan prompt (steps the agent lays out and follows),
    -- 3 internal = the agent's own machinery prompt (planner system,
    -- step addendum, fork addenda — seeded from the Python constants,
    -- editable in the prompt view, hidden from the Skills picker which
    -- lists only skills/plan labels)
    ('prompt_type', 0, 'system', 0),
    ('prompt_type', 1, 'skills', 1),
    ('prompt_type', 2, 'plan',   2),
    ('prompt_type', 3, 'internal', 3),
    -- agent_context_kind: text.deep_agent_contexts.kind — which payload
    -- a context row carries. Code order = the run's data flow: the plan
    -- node's plan JSON, each step's input notes and tick result note, a
    -- spawned subagent's final answer, and the runner's HARDCODED result
    -- node's fixed output — the run's final answer document, which the
    -- UI's Result view reads as the display result (runner.py's result
    -- node — the graph's last node, plan → execute → finalize → result).
    -- Third text.* LLM-layer enum converted (2026-10-31)
    ('agent_context_kind', 0, 'plan',            0),
    ('agent_context_kind', 1, 'step-input',      1),
    ('agent_context_kind', 2, 'step-result',     2),
    ('agent_context_kind', 3, 'subagent-result', 3),
    ('agent_context_kind', 4, 'final-result',    4),
    -- search_table: text.search_lexicon_df.search_table — which
    -- searchable table a high-DF lexeme row counts. Code order =
    -- TSV_SPECS order (_common/tokenization/search_indexing.py): the
    -- news corpus first, then the Q&A tables. Fourth text.* vocabulary
    -- (2026-11-01)
    ('search_table', 0, 'news',              0),
    ('search_table', 1, 'news_comments',     1),
    ('search_table', 2, 'news_digestions',   2),
    ('search_table', 3, 'llm_qa',            3),
    ('search_table', 4, 'llm_qa_by_ask',     4),
    ('search_table', 5, 'llm_qa_by_research', 5);
