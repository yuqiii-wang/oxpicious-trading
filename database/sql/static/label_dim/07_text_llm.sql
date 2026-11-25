-- ============================================================================
--  static.label_dim seed — TEXT / LLM-LAYER vocabularies
--
--  The llm-schema LLM-layer knowledge-base closed vocabularies. Owning tables:
--  llm.llm_qa_by_research (research_logic — the dominant investing
--  logic a research-logic Q&A row classifies), llm.ai_prompts
--  (prompt_type — the prompt row's role in a deep-agent run),
--  llm.deep_agent_contexts (agent_context_kind — which payload a
--  context row carries, incl. the graphs' final output nodes),
--  llm.deep_agent_langgraph_templates (langgraph_type — which of a
--  run's three chained LangGraphs a template drives),
--  text.news_digestions (news_category — the discourse-category read
--  of the fake-news detector's classification lane) and
--  text.news_providers (news_type — the origin's media type: social
--  media, financial report, news article, …), and the text-analytics
--  reference vocabulary static.fin_sentiment_dict (fin_dict_polarity —
--  the CFSD dictionary word polarity). First domain of
--  the schema's LLM-layer enums converted
--  to the label-code contract (2026-10-27); further llm.* conversions
--  register here.
-- ============================================================================

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- research_logic: llm.llm_qa_by_research.logic_label — the dominant
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
    -- prompt_type: llm.ai_prompts.prompt_type — the row's role in a
    -- deep-agent run: 0 system = the run's standing prompt, 1 skills =
    -- an approach instruction (the UI Skills picker's rows), 2 plan = a
    -- generation-plan prompt (steps the agent lays out and follows),
    -- 3 internal = the agent's own machinery prompt (planner system,
    -- step addendum, spawn addenda — seeded from the Python constants,
    -- editable in the prompt view, hidden from the Skills picker which
    -- lists only skills/plan labels), 4 tool = a tool description
    -- (one row per model-callable tool — the tool classes' schema
    -- description, seeding static/ai_prompts/10_tool_prompts.sql and
    -- overriding the built-in text fail-soft at run start; hidden from
    -- the Skills picker like internal). tool added 2026-11-02
    ('prompt_type', 0, 'system', 0),
    ('prompt_type', 1, 'skills', 1),
    ('prompt_type', 2, 'plan',   2),
    ('prompt_type', 3, 'internal', 3),
    ('prompt_type', 4, 'tool', 4),
    -- agent_context_kind: llm.deep_agent_contexts.kind — which payload
    -- a context row carries. Code order = the run's data flow: the plan
    -- node's plan JSON, each step's input notes and tick result note, a
    -- spawned subagent's final answer, the runner's HARDCODED result
    -- node's fixed output — the run's final answer document, which the
    -- UI's Result view reads as the display result (the RESULT graph's
    -- final output node in the three-graph chain — plan → custom →
    -- result) — the supervisor node's main-path review, and a middle
    -- graph's final output node payload (the CUSTOM graph's synthesis —
    -- what the next graph in the chain reads as its input).
    -- Third llm.* LLM-layer enum converted (2026-10-31);
    -- supervisor-review added 2026-11-02; graph-output added 2026-11-06
    ('agent_context_kind', 0, 'plan',            0),
    ('agent_context_kind', 1, 'step-input',      1),
    ('agent_context_kind', 2, 'step-result',     2),
    ('agent_context_kind', 3, 'subagent-result', 3),
    ('agent_context_kind', 4, 'final-result',    4),
    ('agent_context_kind', 5, 'supervisor-review', 5),
    ('agent_context_kind', 6, 'graph-output',    6),
    -- langgraph_type: llm.deep_agent_langgraph_templates.type — which
    -- of a run's three chained LangGraphs a template drives (the
    -- three-graph refactor, 2026-11-06): 0 plan = a saved plan template
    -- (reuse skips the planning session), 1 custom = a saved
    -- custom-graph step set (reserved — the on-demand graph the planner
    -- builds is type custom at runtime), 2 result = the result graph's
    -- fixed four-review-wave definition (the seeded default-result-wave
    -- row). Fifth llm.* vocabulary converted (2026-11-06)
    ('langgraph_type', 0, 'plan',   0),
    ('langgraph_type', 1, 'custom', 1),
    ('langgraph_type', 2, 'result', 2),
    -- agent_step_type: the Thread Graph's "agent steps" list
    -- (sql_views .../ai-thread/state_agent_steps.sql) — which kind of
    -- step a row is: 0 inference = one LLM request (a chat completion
    -- through the provider), 1 spawn = one spawned sub node collapsed —
    -- input is the fresh prompt the spawn was given, output is the sub
    -- node's FINAL output (its answer or error); the sub node's own
    -- intermediates stay at the child node. 2 decision = one System
    -- One (jev) decision read (typed facts in, calibrated verdict out
    -- — llm.deep_agent_aux_steps), 3 tokenization = one StructBERT
    -- segmentation op (search_tsv / context_tags / query_segment
    -- lanes) — both NEVER loaded into an LLM context, UI-display
    -- only. Sixth llm.* vocabulary (2026-11-12; aux types 2026-11-13)
    ('agent_step_type', 0, 'inference', 0),
    ('agent_step_type', 1, 'spawn', 1),
    ('agent_step_type', 2, 'decision', 2),
    ('agent_step_type', 3, 'tokenization', 3),
    -- 4 fork (2026-11-13) = one inherit-context sub node collapsed (the
    -- FORK counterpart of code 1: the child CONTINUED a deep copy of
    -- its parent's transcript — llm.deep_agent_states.child_kind 1 —
    -- instead of starting fresh; the UI's agent-steps list and the
    -- Thread Graph canvas label it fork)
    ('agent_step_type', 4, 'fork', 4),
    -- agent_child_kind: llm.deep_agent_states.child_kind — how a child
    -- state row (parent_state_id set) was raised (2026-11-13). 0 spawn
    -- = a FRESH-context sub node (spawn_subagent / the router's
    -- scripting handoff: goal + business context only, never the
    -- parent transcript); 1 fork = an INHERIT-context sub node
    -- (fork_subagent / the router's drift-fork lane: continues a deep
    -- copy of the parent's PRE-DRIFT transcript plus a fork directive,
    -- foreground — the parent awaits); 2 abandoned = the drift fork's
    -- ABANDONED pre-fork tail stub (the drifted segment after the fork
    -- point, settled skipped with the drift note — the canvas demotes
    -- it to the right-side edge path beside the forked continuation);
    -- 3 resume (2026-11-15) = the SUPERVISOR FAILURE-FORK RECORD —
    -- a record row the repair lane writes under a failed step it
    -- handled (the fork action on the UI: the failed attempt as a RED
    -- DEAD-END BRANCH, settled failed, one dashed leg in): the retry
    -- writes it as the flip clears the parent's error; a no-action
    -- verdict writes it beside the still-failed row. Carries the prior
    -- attempt's error and the verdict note as its title (``supervisor
    -- resume — …`` | ``supervisor no-action``); never routed, excluded
    -- from the summary-run step counts.
    -- NULL = a top-level row (not a child); child rows predating the
    -- column read as spawn. Seventh llm.* vocabulary
    ('agent_child_kind', 0, 'spawn', 0),
    ('agent_child_kind', 1, 'fork', 1),
    ('agent_child_kind', 2, 'abandoned', 2),
    ('agent_child_kind', 3, 'resume', 3),
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

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- news_category: text.news_digestions.category — the discourse
    -- category of a digested article (the fake-news detector's FIRST
    -- lane, 2026-11-17): what KIND of text the item is decides what
    -- "fake" can even mean — a question or a hedged opinion cannot be
    -- fake the way an explicit call can. Written by the Jev (System
    -- One) category read over the article's source + title + content
    -- (llm_agents.news_digestions.fake_news). 0 discussion = the item
    -- asks a question / invites discussion (most zhihu items — 明天
    -- 怎么走？); 1 analysis = evidence-based market analysis (data,
    -- recap, reasoned logic); 2 opinion = a personal stance or mood
    -- WITHOUT a definite call (不悲观); 3 statement = an EXPLICIT
    -- directional call about the market's next move (明天涨 / 明天必跌)
    -- — the only category the disingenuous-question fake verdict
    -- builds on (asking 明天怎么走 while the same origin's earlier
    -- items already STATED the direction); 99 other = the read was
    -- not confident enough to classify (also the pre-column default
    -- legacy digestion rows carry). Eighth vocabulary in this file
    -- (2026-11-17)
    ('news_category',  0, 'discussion', 0),
    ('news_category',  1, 'analysis',   1),
    ('news_category',  2, 'opinion',    2),
    ('news_category',  3, 'statement',  3),
    ('news_category', 99, 'other',     99);

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- news_type: text.news_providers.news_type — the origin's MEDIA type
    -- (2026-11-21): what kind of outlet an origin is, read against the
    -- audit metrics (a fake share concentrated in social_media origins is
    -- the expected shape; the same share in financial_report is the
    -- alarm). Derived from text.news.source by the registry sync's
    -- classify (analysis/news_audit/config.py — curated platform list +
    -- substring guards; everything unmapped defaults to news_article,
    -- the scraped-portal norm). 0 news_article = news portals and
    -- official news items (gov, ndrc, the web-scraped portals);
    -- 1 financial_report = official monetary-policy / financial data
    -- releases (pboc_lpr / pboc_omo / pboc_oma); 2 social_media = UGC
    -- platforms (zhihu, xueqiu, weibo, guba/caifuhao, 淘股吧, 韭研公社,
    -- bilibili, the broker communities); 3 ai_digest = the repo's own
    -- AI daily digest (ai_daily); 99 other = unrecognized.
    ('news_type',  0, 'news_article',     0),
    ('news_type',  1, 'financial_report', 1),
    ('news_type',  2, 'social_media',     2),
    ('news_type',  3, 'ai_digest',        3),
    ('news_type', 99, 'other',           99);

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- fin_dict_polarity: static.fin_sentiment_dict.polarity — the CFSD
    -- (中文金融情感词典, 姜富伟/孟令超/唐国豪, 8,793 words — LM translate +
    -- word2vec expand, A-share validated) word's polarity (2026-11-21).
    -- Code order = sign order. 2 both = the 21 words the source lists in
    -- BOTH its positive and negative sheets (context-dependent sign,
    -- e.g. 冷静/大胆/揭示) — the PK(word) table cannot carry two rows for
    -- one word, so they get their own code instead of a silent
    -- negative-wins. Tenth vocabulary in this file
    ('fin_dict_polarity', 0, 'negative', 0),
    ('fin_dict_polarity', 1, 'positive', 1),
    ('fin_dict_polarity', 2, 'both',     2);

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- news_scope_kind: text.news_text_daily.scope_kind — which scope a
    -- daily news-text metric row speaks for (2026-11-22). Code order =
    -- market breadth: 0 market_all = every digested article that day,
    -- read against the SSE Composite; 1 market_recap = the BROAD_SSE-
    -- tagged broad-market recap subset (the source-class split the CFSD
    -- paper keeps — official recaps vs the whole corpus); 2 industry =
    -- EVENT-DAY rows per industry_id (a row only on days the industry
    -- has digested articles — the daily-index grain is infeasible at
    -- ~1-2 articles/industry-day); 3 sector = EVENT-DAY rows per
    -- sector_id (the industry rows rolled UP the taxonomy to the L1
    -- parent sector — text.news.sector_id; same event-day framing,
    -- denser per day because a sector pools its member industries'
    -- coverage). Eleventh vocabulary in this file
    ('news_scope_kind', 0, 'market_all',   0),
    ('news_scope_kind', 1, 'market_recap', 1),
    ('news_scope_kind', 2, 'industry',     2),
    ('news_scope_kind', 3, 'sector',       3);
