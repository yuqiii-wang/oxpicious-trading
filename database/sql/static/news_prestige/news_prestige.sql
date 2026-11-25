-- ============================================================================
--  News provider prestige — static curation metadata.
--  Table: static.news_prestige — one curated row per news origin grain:
--  the provider-level row (author = '') plus optional exact (provider,
--  author) overrides. prestige is a JSON payload — rank (INTEGER,
--  display order, lower first), tier (the text.news_providers news_type
--  vocabulary: news_article / financial_report / social_media /
--  ai_digest, plus aggregator for the catch-all web sources), label
--  (the Chinese display label shown as the UI chip tooltip). Ordering
--  source of record for the News page's provider/author chip rows
--  (/api/news/providers + /api/news/authors): origins without a row
--  order AFTER the ranked ones, by article count. Curated by hand —
--  never touched by the analysis.news_audit registry sync; re-apply =
--  DELETE all + INSERT (01_seed_prestige.sql). provider mirrors the
--  audit registry's "provider" concept (= text.news.source — the news
--  vocabulary calls the column source, the UI calls it Provider).
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The curated prestige registry for news providers — a display rank, media tier and Chinese label per provider (and per provider+author origin) that orders the news UI's provider and author chips.
-- tags: provider, 提供方, prestige, 权威度, news, 新闻, display order, 显示顺序, author, 作者, curation, 策展
-- ============================================================================

CREATE TABLE IF NOT EXISTS static.news_prestige (
    provider  TEXT        NOT NULL,             -- = text.news.source (gov, zhihu, 东方财富网, ...)
    author    TEXT        NOT NULL DEFAULT '',  -- '' = provider-level row; else exact-origin override
    prestige  JSONB       NOT NULL,             -- {"rank": 3, "tier": "financial_report", "label": "央行公告"}
    CONSTRAINT pk_news_prestige PRIMARY KEY (provider, author)
);

COMMENT ON TABLE static.news_prestige IS
  'Curated news-provider prestige — the display rank / media tier / label '
  'per provider (and optionally per provider+author origin) that orders '
  'the News page''s provider and author chips. Static curation: hand-'
  'edited seed files, never touched by the news_audit registry sync.';

COMMENT ON COLUMN static.news_prestige.provider IS
  'The news provider — the text.news.source value (gov, ndrc, pboc_lpr, '
  'pboc_omo, pboc_oma, zhihu, ai_daily, or a web-media name like '
  '东方财富网). Named provider (not source) to match the audit registry''s '
  'vocabulary and the UI''s Provider chips.';

COMMENT ON COLUMN static.news_prestige.author IS
  'The origin''s author when the row targets one exact origin (zhihu '
  'answer AuthorName, gov 来源). EMPTY STRING (default) = the '
  'provider-level row every article of the provider falls back to — '
  'same origin grain as text.news_providers (author '''' sentinel, PK '
  'cannot hold NULL). Author chips without an exact row inherit the '
  'provider-level rank.';

COMMENT ON COLUMN static.news_prestige.prestige IS
  'The prestige payload — JSON: rank (INTEGER, display order, lower '
  'first; chips order by rank then article count), tier (the '
  'text.news_providers news_type vocabulary: news_article / '
  'financial_report / social_media / ai_digest, plus aggregator for '
  'catch-all web sources), label (the Chinese display label the UI '
  'shows as the chip tooltip).';
