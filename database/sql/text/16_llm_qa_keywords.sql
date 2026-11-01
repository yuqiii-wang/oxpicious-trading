-- ============================================================================
--  text.llm_qa_keywords + text.llm_qa_keyword_audit — the AI Q&A corpus's
--  keyword audit, the QA twin of text.news_keywords /
--  text.news_keyword_audit.
--
--  One row per (qa post, matched keyword): how many times the keyword
--  occurs in the post's question+answer, extracted by builds.text with the
--  SAME taxonomy and rules as the news audit (static.keyword_maps —
--  substring counting, whole-word English, negation-aware: 并未下跌 does
--  not count). Written ONLY by builds.text's step 4b
--  (upsert.rewrite_qa_keyword_rows) as a full rewrite per run over the
--  ACTIVE rows — the corpus is small (10^2–10^3 posts) and answers can be
--  rewritten in place (upsert_qa) or soft-deleted (is_active), so a full
--  rewrite is cheaper than staleness tracking (the same rationale as
--  recompute_keyword_stats). Never written by llm_agents.
--
--  The audit VIEW (plain, never stale — the underlying rows are only ever
--  wholesale-rewritten by the build) rolls the rows up per (date,
--  category_type, category_code) exactly like text.news_keyword_audit:
--  qa_posts = posts mentioning the category's keywords, mentions = total
--  keyword occurrences. It feeds the composites/ai-market-qa Ask AI
--  insight metrics (occurrence counts read against the news audit for AI-
--  vs-news tone divergence).
-- ============================================================================

CREATE TABLE IF NOT EXISTS text.llm_qa_keywords (
    qa_id       BIGINT      NOT NULL,
    keyword     TEXT        NOT NULL,
    date        DATE        NOT NULL,  -- the qa_date Shanghai day (the AI page calendar's convention)
    industry_id TEXT,                  -- denormalized from text.llm_qa
    count       INTEGER     NOT NULL,  -- keyword occurrences in question + answer
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_llm_qa_keywords PRIMARY KEY (keyword, qa_id),
    CONSTRAINT fk_llm_qa_keywords_qa FOREIGN KEY (qa_id)
        REFERENCES text.llm_qa (qa_id)
);

CREATE INDEX IF NOT EXISTS ix_llm_qa_keywords_qa_id
    ON text.llm_qa_keywords (qa_id);

COMMENT ON TABLE text.llm_qa_keywords IS
  'Per-QA-post keyword occurrences over the same seeded vocabulary as the '
  'news audit (static.keyword_maps; negation-aware extraction by '
  'builds.text.upsert.rewrite_qa_keyword_rows — the only writer, full '
  'rewrite per run over ACTIVE rows). Feeds '
  'text.llm_qa_keyword_audit and the composites/ai-market-qa insight '
  'metrics.';

-- Re-apply = DROP + CREATE (a view definition change never propagates
-- through IF NOT EXISTS / CREATE OR REPLACE).
DROP VIEW IF EXISTS text.llm_qa_keyword_audit;

CREATE VIEW text.llm_qa_keyword_audit AS
SELECT k.date,
       m.category_type,              -- 'kw_category_type': 0 industry | 1 market | 2 broadmarket (skip rows are never extracted)
       m.category_code,              -- 'kw_market' when type=1, 'kw_theme' when type=2, NULL when industry
       COUNT(DISTINCT k.qa_id)::integer AS qa_posts,  -- posts mentioning the category's keywords
       SUM(k.count)::bigint                            AS mentions   -- total keyword occurrences
FROM text.llm_qa_keywords k
JOIN static.keyword_maps m USING (keyword)
GROUP BY 1, 2, 3;

COMMENT ON VIEW text.llm_qa_keyword_audit IS
  'Daily keyword-audit rollup over the AI Q&A corpus (text.llm_qa_keywords '
  'JOIN static.keyword_maps): per (date, category_type, category_code) how '
  'many ACTIVE QA posts mention the category''s keywords (qa_posts) and '
  'how many times they occur (mentions). A plain VIEW — the build '
  'wholesale-rewrites the underlying rows; feeds the '
  'composites/ai-market-qa Ask AI insight metrics.';
