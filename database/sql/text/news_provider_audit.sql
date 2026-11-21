-- ============================================================================
--  News provider audit
--  Table: text.news_provider_audit — one row per 5-DAY window per news
--  ORIGIN (source, author; author = '' is the provider-level row): how
--  many items the origin published in the window, the average LLM
--  sentiment of the digested ones, the fake-news share, and the origin's
--  industry spread.
--
--  Window grid: 5 CALENDAR-DAY windows, stride 5 (non-overlapping tiling)
--  anchored at DATE '2000-01-03' — a window covers [window_start,
--  window_start + 4 days] and window starts land every 5 days from the
--  anchor, so the grid is stable across runs. Windows are keyed on
--  text.news.date (the publish DAY, Asia/Shanghai) — news publishes on
--  calendar days, weekends included.
--
--  Incremental contract (analysis.news_audit): a COMPLETE window
--  (is_complete = true, window_end <= latest news date) computes once and
--  is never recomputed, EXCEPT within the run's grace period (recent
--  windows recompute to absorb late LLM digestions — verdicts arrive
--  fail-soft after the article). The TRAILING partial window (the one
--  containing the latest news date) recomputes on every run and carries
--  is_complete = false until its 5 days are past.
--
--  Denominators: sentiment/fake verdicts live only on digested rows, so
--  avg_sentiment and fake_pct are over num_digested (NULL when 0), while
--  num_items counts ALL of the origin's articles in the window.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: 5-day news provider audit — per window and origin: item counts, average sentiment, fake-news share and industry spread.
-- tags: audit, 审计, provider, 提供方, author, 作者, sentiment, 情绪, fake news, 假新闻, window, 窗口, news, 新闻


CREATE TABLE IF NOT EXISTS text.news_provider_audit (
    window_start   DATE              NOT NULL,   -- window start on the 5d grid (anchor 2000-01-03 + 5k days)
    window_end     DATE              NOT NULL,   -- window_start + 4 days
    source         TEXT              NOT NULL,   -- provider: text.news.source
    author         TEXT              NOT NULL DEFAULT '',   -- '' = provider-level row
    is_complete    BOOLEAN           NOT NULL,   -- false = trailing partial window (recomputed every run)
    num_items      INTEGER           NOT NULL,   -- ALL of the origin's articles dated in the window
    num_digested   INTEGER           NOT NULL,   -- articles with a text.news_digestions row
    num_fake       INTEGER           NOT NULL,   -- digested articles flagged is_fake_news
    fake_pct       NUMERIC(6,4),                  -- num_fake / num_digested, fraction 0..1; NULL when num_digested = 0
    avg_sentiment  DOUBLE PRECISION,              -- AVG(digestions.sentiment_level) over digested rows; NULL when none
    avg_word_count DOUBLE PRECISION,              -- AVG(news.word_count) over ALL items (NULL word_count rows skipped)
    num_industries INTEGER,                       -- COUNT(DISTINCT industry_id) over the window's items ('' counts as a value)
    computed_at    TIMESTAMPTZ       NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_provider_audit PRIMARY KEY (window_start, source, author),
    CONSTRAINT fk_news_provider_audit_providers FOREIGN KEY (source, author)
        REFERENCES text.news_providers (source, author)
);

-- Per-origin history walks (the provider drill-down on the News Audit page).
CREATE INDEX IF NOT EXISTS ix_news_provider_audit_origin
    ON text.news_provider_audit (source, author, window_start DESC);

COMMENT ON COLUMN text.news_provider_audit.window_start IS
  'Window start on the 5-calendar-day grid: DATE ''2000-01-03'' + 5k days '
  '(the fixed epoch anchor keeps the tiling stable across runs). The '
  'window covers [window_start, window_start + 4 days] inclusive.';

COMMENT ON COLUMN text.news_provider_audit.is_complete IS
  'True when window_end <= the latest text.news.date at compute time — '
  'all 5 calendar days are past. The trailing partial window (containing '
  'the latest news date) carries false and is recomputed on every '
  'analysis.news_audit run until complete.';

COMMENT ON COLUMN text.news_provider_audit.num_items IS
  'ALL articles of the origin dated within the window — the denominators '
  'for reach; not conditioned on having a digestion.';

COMMENT ON COLUMN text.news_provider_audit.num_digested IS
  'Articles with a text.news_digestions row (sentiment/fake verdicts '
  'exist only on digested rows). avg_sentiment and fake_pct use THIS as '
  'their denominator.';

COMMENT ON COLUMN text.news_provider_audit.fake_pct IS
  'Share of digested articles flagged fake: num_fake / num_digested, a '
  '0..1 fraction rounded to 4dp. NULL when num_digested = 0 (no verdicts
  — e.g. windows before the digestion pipeline existed).';

COMMENT ON COLUMN text.news_provider_audit.avg_sentiment IS
  'Average text.news_digestions.sentiment_level over the window''s '
  'digested rows, in [-5, +5] (NULL when none).';

COMMENT ON COLUMN text.news_provider_audit.num_industries IS
  'COUNT(DISTINCT industry_id) over the window''s items (industry_id ''
  '' = untagged counts as one value) — the origin''s industry spread.';

COMMENT ON TABLE text.news_provider_audit IS
  '5-day window audit per news origin (source, author; author = '''' is '
  'the provider-level row): num_items / num_digested / num_fake / '
  'fake_pct / avg_sentiment / avg_word_count / num_industries. Windows '
  'are 5-calendar-day non-overlapping tiles anchored at 2000-01-03; '
  'complete windows compute once, the trailing partial window and the '
  'grace-period recent windows recompute. Built by analysis.news_audit '
  'from text.news LEFT JOIN text.news_digestions.';
