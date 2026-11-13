
-- ============================================================================
--  Per-article keyword frequencies + per-keyword corpus stats. PK is
--  (keyword, news_id): one row per article per keyword. The former
--  news_keyword_index table was folded in here: doc_freq / idf are
--  keyword-level stats duplicated onto EVERY row of that keyword (keyword is
--  not unique, the stats are constant within one keyword). The Python insert
--  pipeline recalculates them for all rows of each keyword it touches — and
--  for all keywords when the total article count changed, since IDF is
--  corpus-level. No SQL functions/triggers: the pipeline owns the writes.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Per-article keyword frequencies with corpus-wide document frequencies and idf — the search and audit vocabulary in action.
-- tags: keywords, 关键词, idf, search, 检索, audit, 审计

CREATE TABLE IF NOT EXISTS text.news_keywords (
    news_id               BIGINT      NOT NULL,
    keyword               TEXT        NOT NULL,
    date                  DATE        NOT NULL,  -- denormalized for join convenience
    industry_id           TEXT,                  -- denormalized from text.news
    today_industry_change DOUBLE PRECISION,      -- industry's change on this date or next date if today is holiday
    count                 INTEGER,               -- raw count in the article
    count_pct             NUMERIC(6,4),          -- count / article_word_count
    doc_freq              INTEGER,               -- # articles containing keyword
    idf                   NUMERIC(12,6),         -- ln(1 + N / doc_freq)
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_keywords PRIMARY KEY (keyword, news_id),
    CONSTRAINT fk_news_keywords_news FOREIGN KEY (news_id)
        REFERENCES text.news (news_id)
);


CREATE INDEX IF NOT EXISTS ix_news_keywords_news_id
    ON text.news_keywords (news_id);

COMMENT ON COLUMN text.news_keywords.doc_freq IS
  'Document frequency of the keyword: number of articles containing it. '
  'Constant within one keyword (duplicated on each of its rows); maintained '
  'by the Python insert pipeline.';

COMMENT ON COLUMN text.news_keywords.idf IS
  'Inverse document frequency ln(1 + N / doc_freq), N = total article count. '
  'Constant within one keyword (duplicated on each of its rows); higher = '
  'rarer / more significant keyword.';

-- today_industry_change refresh joins (industry_id, date) per touched article.
CREATE INDEX IF NOT EXISTS ix_news_keywords_industry_date
    ON text.news_keywords (industry_id, date);
