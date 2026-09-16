-- ============================================================================
--  LLM news digestions
--  Table: text.news_digestions — one LLM digestion per article: a summary,
--  a sentiment_level score in [-5, 5], and the model that produced it.
--
--  One row per news_id (PK = news_id): a re-digestion with a newer model
--  UPSERTs over the old row rather than keeping history. industry_id and
--  date are denormalized from text.news (same pattern as text.news_keywords)
--  so industry/day-level sentiment queries don't need the news join.
-- ============================================================================

CREATE TABLE IF NOT EXISTS text.news_digestions (
    news_id         BIGINT            NOT NULL,   -- parent article (text.news)
    date            DATE              NOT NULL,   -- denormalized from text.news
    industry_id     TEXT,                         -- denormalized from text.news
    summary         TEXT              NOT NULL,   -- LLM digestion of the article
    sentiment_level DOUBLE PRECISION,             -- LLM sentiment score, [-5, 5]
    llm_model       TEXT,                         -- model that produced the digestion
    created_at      TIMESTAMPTZ       NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_digestions PRIMARY KEY (news_id),
    CONSTRAINT fk_news_digestions_news FOREIGN KEY (news_id)
        REFERENCES text.news (news_id),
    CONSTRAINT ck_news_digestions_sentiment
        CHECK (sentiment_level IS NULL OR
               sentiment_level >= -5 AND sentiment_level <= 5)
);

-- Industry/day sentiment rolls and UI nav (mirrors ix_news_industry_date).
CREATE INDEX IF NOT EXISTS ix_news_digestions_industry_date
    ON text.news_digestions (industry_id, date DESC);

COMMENT ON COLUMN text.news_digestions.news_id IS
  'Parent article in text.news. Primary key: one digestion per article; '
  're-digesting an article with a newer model overwrites its row.';

COMMENT ON COLUMN text.news_digestions.date IS
  'The article''s date, denormalized from text.news for join convenience '
  '(same pattern as text.news_keywords.date).';

COMMENT ON COLUMN text.news_digestions.industry_id IS
  'The article''s industry tag, denormalized from text.news.';

COMMENT ON COLUMN text.news_digestions.sentiment_level IS
  'LLM-assigned sentiment score in [-5, 5] (-5 = most negative, +5 = most '
  'positive, 0 = neutral). NULL when the digestion produced no score. '
  'Guarded by ck_news_digestions_sentiment.';

COMMENT ON TABLE text.news_digestions IS
  'Per-article LLM digestion: summary + sentiment_level in [-5, 5], linked '
  'to its text.news row by news_id. Populated by the LLM digestion pipeline, '
  'not by builds.text.';
