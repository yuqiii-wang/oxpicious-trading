-- ============================================================================
--  LLM news digestions
--  Table: text.news_digestions — one LLM digestion per article: a summary,
--  a sentiment_level score in [-5, 5], its jev component reads
--  (P利好 / P利空 / intensity), a discourse category, a fake-news
--  verdict, and the model that produced it.
--
--  One row per news_id (PK = news_id): a re-digestion with a newer model
--  UPSERTs over the old row rather than keeping history. industry_id and
--  date are denormalized from text.news (same pattern as text.news_keywords)
--  so industry/day-level sentiment queries don't need the news join.
--
--  Components: the jev path (llm_agents.news_digestions.jev_digest) reads
--  direction as the difference of two noul probabilities and magnitude as
--  an expected intensity level, then composes
--  sentiment_level = (p_bullish − p_bearish) · 5 ·
--                    (0.35 + 0.65 · intensity_score/3).
--  The components are stored alongside the product (RavenPack-style
--  orthogonal fields) so aggregations can weight/aggregate them
--  independently and the composition can be recalibrated without
--  re-digesting. NULL for the generative chat path (glm) and legacy rows
--  not yet re-digested.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Per-article LLM digestion — a summary, a sentiment score with its jev component reads (bullish/bearish probabilities and impact-intensity level), a discourse category (discussion/analysis/opinion/statement) and a fake-news verdict for each piece.
-- tags: digestion, 摘要, llm, sentiment, 情绪, fake news, 假新闻, category, 类别, discussion, 讨论, statement, 断言, news, 新闻, 利好, 利空, 强度, intensity


CREATE TABLE IF NOT EXISTS text.news_digestions (
    news_id         BIGINT            NOT NULL,   -- parent article (text.news)
    date            DATE              NOT NULL,   -- denormalized from text.news
    industry_id     TEXT,                         -- denormalized from text.news
    summary         TEXT              NOT NULL,   -- LLM digestion of the article
    sentiment_level DOUBLE PRECISION,             -- LLM sentiment score, [-5, 5]
    p_bullish       DOUBLE PRECISION,             -- jev noul read: P(利好) in [0, 1]
    p_bearish       DOUBLE PRECISION,             -- jev noul read: P(利空) in [0, 1]
    intensity_score DOUBLE PRECISION,             -- jev score read: expected level 0..3 over [忽略, 轻微, 明显, 重大]
    category        SMALLINT          NOT NULL DEFAULT 99,   -- label code (static.label_dim 'news_category'): 0 discussion 1 analysis 2 opinion 3 statement 99 other
    is_fake_news    BOOLEAN           NOT NULL DEFAULT false,   -- fake-news detector verdict (disingenuous tomorrow-question)
    llm_model       TEXT,                         -- model that produced the digestion
    created_at      TIMESTAMPTZ       NOT NULL DEFAULT now(),
    search_tsv      TSVECTOR,                     -- weighted full-text search vector: summary B

    CONSTRAINT pk_news_digestions PRIMARY KEY (news_id),
    CONSTRAINT fk_news_digestions_news FOREIGN KEY (news_id)
        REFERENCES text.news (news_id),
    CONSTRAINT ck_news_digestions_sentiment
        CHECK (sentiment_level IS NULL OR
               sentiment_level >= -5 AND sentiment_level <= 5),
    CONSTRAINT ck_news_digestions_reads
        CHECK ((p_bullish IS NULL OR p_bullish BETWEEN 0 AND 1) AND
               (p_bearish IS NULL OR p_bearish BETWEEN 0 AND 1) AND
               (intensity_score IS NULL OR intensity_score BETWEEN 0 AND 3))
);

-- Industry/day sentiment rolls and UI nav (mirrors ix_news_industry_date).
CREATE INDEX IF NOT EXISTS ix_news_digestions_industry_date
    ON text.news_digestions (industry_id, date DESC);

-- Weighted full-text search — same contract as
-- text.news.search_tsv (the query must be segmented the same way).
CREATE INDEX IF NOT EXISTS ix_news_digestions_search_tsv_gin
    ON text.news_digestions USING gin (search_tsv);

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
  'Guarded by ck_news_digestions_sentiment. For the jev path this is the '
  'COMPOSED value (p_bullish − p_bearish) · 5 · (0.35 + 0.65 · '
  'intensity_score/3); the chat path scores it directly.';

COMMENT ON COLUMN text.news_digestions.p_bullish IS
  'Jev (onejev-1.6-4b) noul read: P(该新闻对相关行业构成利好), in [0, 1]. '
  'One component of sentiment_level (see the composition in the table '
  'banner). NULL for the generative chat path (glm) and legacy rows not '
  'yet re-digested with the components. Guarded by '
  'ck_news_digestions_reads.';

COMMENT ON COLUMN text.news_digestions.p_bearish IS
  'Jev (onejev-1.6-4b) noul read: P(该新闻对相关行业构成利空), in [0, 1]. '
  'Direction = p_bullish − p_bearish (the difference of two independent '
  'yes/no reads). NULL for the chat path / legacy rows. Guarded by '
  'ck_news_digestions_reads.';

COMMENT ON COLUMN text.news_digestions.intensity_score IS
  'Jev score read over the 4-level impact-intensity criteria '
  '[忽略, 轻微, 明显, 重大]: the expected 0-based level in [0, 3] with '
  'per-level probabilities server-side. Magnitude scaling of '
  'sentiment_level. NULL for the chat path / legacy rows. Guarded by '
  'ck_news_digestions_reads.';

COMMENT ON COLUMN text.news_digestions.category IS
  'Discourse category of the article — the fake-news detector''s '
  'classification lane, a Jev (System One) read over source + title + '
  'content (llm_agents.news_digestions.fake_news). Decodes via '
  'static.label_dim ''news_category'': 0 discussion = asks / invites '
  'discussion (most zhihu items), 1 analysis = evidence-based market '
  'analysis, 2 opinion = personal stance without a definite call, '
  '3 statement = an EXPLICIT directional call (明天涨) — the only category '
  'the fake verdict builds on, 99 other = read not confident (also the '
  'pre-column default legacy rows carry).';

COMMENT ON COLUMN text.news_digestions.is_fake_news IS
  'Fake-news detector verdict (true = flagged fake), written by the '
  'digestion pipeline''s fake_news detector alongside the category read. '
  'The implemented rule is the DISINGENUOUS TOMORROW-QUESTION: the title '
  'asks about the next day''s move (明天怎么走？) while earlier items of '
  'the same origin (author when captured, else source) within the '
  'detector''s lookback window already made EXPLICIT directional '
  'statements — opinions/sentiments never trigger it, only statements.';

COMMENT ON COLUMN text.news_digestions.search_tsv IS
  'Weighted search tsvector (simple config over segmented text): summary B. '
  'Same contract as text.news.search_tsv.';

COMMENT ON TABLE text.news_digestions IS
  'Per-article LLM digestion: summary + sentiment_level in [-5, 5] with '
  'its jev component reads (p_bullish / p_bearish / intensity_score) + '
  'discourse category (news_category) + fake-news verdict, linked '
  'to its text.news row by news_id. Populated by the LLM digestion pipeline, '
  'not by builds.text.';
