-- ============================================================================
--  AI prompts seed — skills / text_corpus: the TEXT-CORPUS AUDIT
--  guides — how to work the AI QA knowledge base (llm.llm_qa) and the
--  news corpus (text.news / text.news_comments) as an auditor rather
--  than a retriever: fix the dates of interest, SAMPLE a few entries,
--  verify the samples against the data, and dive into anything
--  suspicious with a dedicated subagent. One of the static/ai_prompts/
--  seed files — DDL + the shared contract: 06_ai_prompts.sql.
--
--  Both guides carry bound_labels (the force-load keywords matched
--  against the run's question) and optional_labels (the English search
--  vocabulary; contract: 06_ai_prompts.sql) — the corpus domains are
--  question-bound like the scope-bound biz_guide guides.
--
--  Idempotent — re-running refreshes the seeded texts in place. The
--  version column stays at its seeded 1 and is deliberately NOT in the
--  ON CONFLICT SET: a re-run never resets a manually bumped version
--  (the UI edit path bumps it).
-- ============================================================================

INSERT INTO llm.ai_prompts (prompt_key, prompt_sub_type, prompt_type, title, version, instruction, preview, note, bound_labels, optional_labels, ordinal) VALUES
    ('ai-qa-audit',
     'text_corpus',
     1,
     'AI QA audit',
     1,
     'ai-qa-audit: the AI QA corpus (llm.llm_qa — the weekly industry '
     'answers; llm_qa_by_research — the research-logic answers; '
     'llm_qa_by_ask — the interactive asks) is this house''s own LLM '
     'OUTPUT — audit it, never trust it wholesale. FIRST fix the dates '
     'of interest: the run''s own window, the big-move days (rows with '
     'is_big_fluctuation), and the most recent trading days in scope. '
     'Then SAMPLE a few answers per date — never read everything: '
     'text_search with date_from/date_to, or db_run_query over '
     'llm.llm_qa ORDER BY qa_date DESC with LIMIT, biasing the sample '
     'toward the risky rows (is_failed_explanation = refusal '
     'boilerplate, has_refs = false = no resolved references) plus a '
     'few healthy ones. For each sampled answer judge: does its claim '
     'match the DB facts for its qa_date (industry moves, signals); is '
     'it substance or boilerplate; do its cited references '
     '(news_group_id → text.news_group_items → text.news) actually '
     'support the answer''s statements. When a sample looks suspicious, '
     'do NOT argue it out inline — spawn_subagent with a SELF-CONTAINED '
     'goal (the qa_id, the question, the exact claim to verify, the '
     'tables to check) and let the fork dive deep; collect it and '
     'report the verdict with the evidence.',
     'Audit the AI QA corpus: fix the dates of interest, sample a few answers, verify claims against the DB, fork a subagent on anything suspicious.',
     'The QA corpus is LLM output — sample, verify, and dive deep via a subagent.',
     ARRAY['问答', 'AI回答', 'qa'],
     ARRAY['ai qa', 'qa audit', 'qa sampling', 'answer audit', 'llm_qa', 'knowledge base'],
     29),
    ('news-audit',
     'text_corpus',
     1,
     'News audit',
     1,
     'news-audit: the news corpus (text.news + the reader comments in '
     'text.news_comments) feeds every downstream digest and QA answer — '
     'audit it for the dates of interest instead of trusting it '
     'wholesale. Fix the dates first: the run''s window, the big-move '
     'days (text.news.is_big_fluctuation — the article''s mapped '
     'industry moved >= 3% that day), and the most recent trading days '
     'in scope. SAMPLE a few articles per date via text_search with '
     'date_from/date_to (raw ILIKE matches nothing — keep the '
     'segmented search), and bias the sample toward the '
     'highest-visibility items: order_by votes ranks by the article '
     'thumbs-up count, order_by comments ranks by the discussion size '
     '(the count rides back as n_comments); on news_comments the same '
     'votes ordering surfaces the top reader comments. For each sample '
     'judge: does the article''s content match its industry_id / date '
     'mapping; is it substantive reporting vs keyword-stuffed '
     'boilerplate; do the top comments contradict its claim. When a '
     'sample looks suspicious, spawn_subagent with a SELF-CONTAINED '
     'goal (the news_id, the exact claim to verify, the tables to '
     'check) to dive deep; collect the fork and report the verdict '
     'with the evidence.',
     'Audit the news corpus: fix the dates of interest, sample the hottest articles (most votes/comments), verify against the data, fork a subagent on anything suspicious.',
     'News feeds every digest and QA answer — sample the hot items, verify, dive deep via a subagent.',
     ARRAY['新闻', '资讯', '舆情'],
     ARRAY['news audit', 'news sampling', 'news comments', 'engagement', 'most commented', 'thumbs up', 'news corpus'],
     30)
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
