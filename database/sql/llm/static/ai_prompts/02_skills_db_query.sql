-- ============================================================================
--  AI prompts seed — skills / db_query: data-access conventions (how
--  to query, how to rank). One of the static/ai_prompts/ seed files —
--  DDL + the shared contract: 06_ai_prompts.sql.
--
--  Idempotent — re-running refreshes the seeded texts in place. The
--  version column stays at its seeded 1 and is deliberately NOT in the
--  ON CONFLICT SET: a re-run never resets a manually bumped version
--  (the UI edit path bumps it).
-- ============================================================================

INSERT INTO llm.ai_prompts (prompt_key, prompt_sub_type, prompt_type, title, version, instruction, preview, note, ordinal) VALUES
    ('db-search',
     'db_query',
     1,
     'DB search',
     1,
     'db-search: ground every claim in the data — start from the '
     'items'' tags (as_of date, scope codes/industry, page keywords) '
     'and their preloaded article excerpts, decide what to check, '
     'inspect schemas (db_list_tables / db_describe_table) and run '
     'narrow aggregate queries (db_run_query) before writing; '
     'kg_search resolves the tables by business meaning when the '
     'owning table is unknown.',
     'Ground every claim in the database — work from the item tags and article excerpts, inspect schemas and run narrow aggregates before writing.',
     'Ground claims in actual DB values via the agent''s read-only tools.',
     1),
    ('rarity-ranking',
     'db_query',
     1,
     'Rarity ranking',
     1,
     'rarity-ranking: weigh each metric against its own trailing '
     'history (percentile within the past ~10y) and call out '
     'top/bottom-tail readings explicitly.',
     'Weigh every metric against its own trailing ~10y history and call out top/bottom-tail readings explicitly.',
     'Flag metrics sitting in the tails of their own history.',
     2)
ON CONFLICT (prompt_key, prompt_sub_type) DO UPDATE
    SET prompt_type = EXCLUDED.prompt_type,
        title       = EXCLUDED.title,
        instruction = EXCLUDED.instruction,
        preview     = EXCLUDED.preview,
        note        = EXCLUDED.note,
        ordinal     = EXCLUDED.ordinal,
        is_active   = true,
        updated_at  = now();
