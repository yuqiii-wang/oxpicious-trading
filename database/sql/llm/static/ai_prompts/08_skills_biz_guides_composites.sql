-- ============================================================================
--  AI prompts seed — skills / biz_guide, COMPOSITES: the composites
--  lenses (opposite-industry offsets, the sentiment text corpus). One
--  of the static/ai_prompts/ seed files — DDL + the shared contract:
--  06_ai_prompts.sql.
--
--  Every scope-bound guide carries its bound_labels (the GIN-indexed
--  force-load keyword array matched against the run's question) and
--  its optional_labels (the GIN-indexed English search vocabulary;
--  contract: 06_ai_prompts.sql).
--
--  Idempotent — re-running refreshes the seeded texts in place. The
--  version column stays at its seeded 1 and is deliberately NOT in the
--  ON CONFLICT SET: a re-run never resets a manually bumped version
--  (the UI edit path bumps it).
-- ============================================================================

INSERT INTO llm.ai_prompts (prompt_key, prompt_sub_type, prompt_type, title, version, instruction, preview, note, bound_labels, optional_labels, ordinal) VALUES
    ('opposite-industry-guide',
     'biz_guide',
     1,
     'Opposite industry correlations guide',
     1,
     'opposite-industry-guide: negatively-correlated industry pairs '
     'are hedging raw material — one leg''s drawdown lands while the '
     'other holds (the pinned 红利 DIV_GENERAL ~ 科技 TECH_GENERAL '
     'showcase is the template pair). A pair''s HEDGE VALUE is '
     'measured, never assumed: walk the pair value curve '
     '(inverse-vol weighted, long-long) and compare its drawdown '
     'against each leg''s own — the hedge-pairs portfolio''s benefit '
     'audit is the standard. Name the pair''s legs, their opposite '
     'score and the trailing window before claiming a hedge; '
     'co-movement drifts, so a stale opposite score is not a hedge.',
     'How to read the opposite-industry offsets — disjoint negatively-correlated industry pairs and their hedge use.',
     'The composites lens (opposite-industry-correlations).',
     ARRAY['负相关', '跷跷板', '行业对冲'],
     ARRAY['opposite industry', 'negative correlation', 'industry pairs', 'hedge value'],
     24),
    ('market-sentiment-guide',
     'biz_guide',
     1,
     'Market sentiment corpus guide',
     1,
     'market-sentiment-guide: sentiment text is CONTEMPORANEOUS '
     'claims, not settled fact — mine the house AI-QA and '
     'news-digestion corpus (text_search full-text) before going to '
     'the web, and treat every reading as a dated sample: quote the '
     'DATE and the source row, never blend rows across market '
     'regimes into one reading, and ground keyword claims in the '
     'audited keyword columns when a claim needs the receipts.',
     'How to mine the house text corpus — AI market QA and sentiment news — for sentiment readings with dates and sources.',
     'The composites sentiment lenses (ai-market-qa, market-sentiment-news).',
     ARRAY['情绪', '舆情', '新闻'],
     ARRAY['market sentiment', 'text corpus', 'ai market qa', 'news digestion', 'sentiment mining'],
     26)
ON CONFLICT (prompt_key, prompt_sub_type) DO UPDATE
    SET prompt_type = EXCLUDED.prompt_type,
        title       = EXCLUDED.title,
        instruction = EXCLUDED.instruction,
        preview     = EXCLUDED.preview,
        note        = EXCLUDED.note,
        bound_labels = EXCLUDED.bound_labels,
        optional_labels = EXCLUDED.optional_labels,
        ordinal     = EXCLUDED.ordinal,
        is_active   = true,
        updated_at  = now();
