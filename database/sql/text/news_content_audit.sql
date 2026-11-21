-- ============================================================================
--  News content audit
--  Table: text.news_content_audit — the content-side twin of
--  text.news_provider_audit: one row per 5-DAY window per INDUSTRY
--  (industry_id = '' = untagged), carrying the same window metrics
--  (items / digested / fake share / average sentiment) plus the keyword
--  columns the provider audit does not have: how many DIFFERENT
--  keywords were hit in the window's content, how many total mentions,
--  and how many of the window's articles hit any keyword at all.
--
--  Same window grid and incremental contract as
--  text.news_provider_audit (see that file's header): 5 calendar-day
--  non-overlapping windows anchored at DATE '2000-01-03', complete
--  windows compute once, trailing partial + grace-period windows
--  recompute (analysis.news_audit).
--
--  Keyword columns come from text.news_keywords (the
--  builds.text KeywordTaxonomy extraction — the curated market/
--  industry/broadmarket vocabulary; negated readings are never written,
--  so mentions count affirmative hits only). distinct_keywords is the
--  count of DIFFERENT keywords over the window (not the per-article sum
--  — one keyword hit by ten articles counts once). The keyword columns
--  are 0 (not NULL) for windows/industries with no keyword rows — the
--  audit write path keeps them int-typed end-to-end.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: 5-day news content audit — per window and industry: item counts, average sentiment, fake share and the keyword hit rollup.
-- tags: audit, 审计, content, 内容, industry, 行业, keyword, 关键词, mentions, 提及, sentiment, 情绪, fake news, 假新闻, window, 窗口


CREATE TABLE IF NOT EXISTS text.news_content_audit (
    window_start           DATE              NOT NULL,   -- window start on the 5d grid (anchor 2000-01-03 + 5k days)
    window_end             DATE              NOT NULL,   -- window_start + 4 days
    industry_id            TEXT              NOT NULL DEFAULT '',   -- '' = untagged articles
    is_complete            BOOLEAN           NOT NULL,   -- false = trailing partial window (recomputed every run)
    num_items              INTEGER           NOT NULL,   -- ALL articles tagged to the industry dated in the window
    num_digested           INTEGER           NOT NULL,   -- articles with a text.news_digestions row
    num_fake               INTEGER           NOT NULL,   -- digested articles flagged is_fake_news
    fake_pct               NUMERIC(6,4),                  -- num_fake / num_digested, fraction 0..1; NULL when num_digested = 0
    avg_sentiment          DOUBLE PRECISION,              -- AVG(digestions.sentiment_level) over digested rows; NULL when none
    distinct_keywords      INTEGER,                       -- COUNT(DISTINCT keyword) over the window's keyword rows
    keyword_mentions       INTEGER,                       -- SUM(text.news_keywords.count) over the window's keyword rows
    articles_with_keywords INTEGER,                       -- COUNT(DISTINCT news_id) among articles that hit >= 1 keyword
    computed_at            TIMESTAMPTZ       NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_content_audit PRIMARY KEY (window_start, industry_id)
);

-- Per-industry history walks (the content drill-down on the News Audit page).
CREATE INDEX IF NOT EXISTS ix_news_content_audit_industry
    ON text.news_content_audit (industry_id, window_start DESC);

COMMENT ON COLUMN text.news_content_audit.industry_id IS
  'The article''s industry tag from text.news (denormalized grain of this '
  'audit). EMPTY STRING = untagged articles — a real bucket (broad-market '
  'recaps and gov items carry no industry), not a data hole.';

COMMENT ON COLUMN text.news_content_audit.is_complete IS
  'True when window_end <= the latest text.news.date at compute time; '
  'the trailing partial window carries false and recomputes every run '
  '(same contract as text.news_provider_audit.is_complete).';

COMMENT ON COLUMN text.news_content_audit.fake_pct IS
  'Share of digested articles flagged fake: num_fake / num_digested, a '
  '0..1 fraction rounded to 4dp. NULL when num_digested = 0.';

COMMENT ON COLUMN text.news_content_audit.distinct_keywords IS
  'How many DIFFERENT text.news_keywords keywords were hit across the '
  'window''s articles (one keyword hit by many articles counts once) — '
  'the vocabulary breadth of the window''s content. 0 when the window '
  'has no keyword rows at all.';

COMMENT ON COLUMN text.news_content_audit.keyword_mentions IS
  'Total affirmative keyword mentions: SUM(text.news_keywords.count) '
  'over the window''s keyword rows (a keyword appearing twice in one '
  'article counts 2). 0 when no keyword rows.';

COMMENT ON COLUMN text.news_content_audit.articles_with_keywords IS
  'How many of the window''s articles hit at least one keyword '
  '(COUNT(DISTINCT news_id) over the keyword rows) — the coverage '
  'denominator to read against num_items. 0 when no keyword rows.';

COMMENT ON TABLE text.news_content_audit IS
  '5-day window audit per industry (industry_id = '''' = untagged): the '
  'provider audit''s item / digested / fake / sentiment metrics plus the '
  'keyword rollup (distinct_keywords, keyword_mentions, '
  'articles_with_keywords from text.news_keywords). Same window grid and '
  'incremental contract as text.news_provider_audit; built by '
  'analysis.news_audit.';
