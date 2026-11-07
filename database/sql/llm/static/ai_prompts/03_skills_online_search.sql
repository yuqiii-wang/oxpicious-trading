-- ============================================================================
--  AI prompts seed — skills / online_search: external-evidence
--  conventions (corpus vs web forks). One of the static/ai_prompts/
--  seed files — DDL + the shared contract: 06_ai_prompts.sql.
--
--  Idempotent — re-running refreshes the seeded texts in place. The
--  version column stays at its seeded 1 and is deliberately NOT in the
--  ON CONFLICT SET: a re-run never resets a manually bumped version
--  (the UI edit path bumps it).
-- ============================================================================

INSERT INTO llm.ai_prompts (prompt_key, prompt_sub_type, prompt_type, title, version, instruction, preview, note, ordinal) VALUES
    ('forked-evidence-search',
     'online_search',
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
     'Fork every external-evidence question into parallel corpus-lane and online-lane subagents and attribute each fact to its lane.',
     'Parallel forks: existing text corpus lane vs live web lane.',
     3)
ON CONFLICT (prompt_key, prompt_sub_type) DO UPDATE
    SET prompt_type = EXCLUDED.prompt_type,
        title       = EXCLUDED.title,
        instruction = EXCLUDED.instruction,
        preview     = EXCLUDED.preview,
        note        = EXCLUDED.note,
        ordinal     = EXCLUDED.ordinal,
        is_active   = true,
        updated_at  = now();
