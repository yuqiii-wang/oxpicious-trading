-- ============================================================================
--  News providers registry
--  Table: text.news_providers — one row per news ORIGIN (source, author):
--  the provider-level row (author = '') plus one row per captured author
--  (zhihu answer authors, gov 来源). Carries the blocklist flag
--  (is_blocked) and the origin's seen-date span, synced from text.news by
--  analysis.news_audit.
--
--  Column naming: "provider" here is text.news.source (gov, zhihu, ndrc,
--  pboc_*, ai_daily) — the repo's news vocabulary calls it source, so the
--  column keeps that name to join/filter text.news and the news API's
--  source filters directly. author uses '' (NOT NULL) as the "no distinct
--  author" sentinel — the same ORIGIN concept the fake-news detector uses
--  (author when captured, else source); PG primary keys cannot hold NULL.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The news origin registry — one row per provider/author pair with the blocklist flag and the origin's seen-date span.
-- tags: provider, 提供方, author, 作者, registry, 名录, blocklist, 黑名单, news, 新闻


CREATE TABLE IF NOT EXISTS text.news_providers (
    source           TEXT        NOT NULL,             -- provider: text.news.source (gov, zhihu, ndrc, ...)
    author           TEXT        NOT NULL DEFAULT '',  -- '' = provider-level row (no distinct author)
    is_blocked       BOOLEAN     NOT NULL DEFAULT false,   -- blocklist flag: manual curation, never touched by the sync
    first_seen_date  DATE,                             -- MIN(text.news.date) over the origin's articles
    last_seen_date   DATE,                             -- MAX(text.news.date) over the origin's articles
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_providers PRIMARY KEY (source, author)
);

COMMENT ON COLUMN text.news_providers.source IS
  'The news provider — text.news.source (gov, ndrc, pboc_lpr, pboc_omo, '
  'pboc_oma, zhihu, ai_daily). Named source (not provider) to match '
  'text.news and the news API''s source filters.';

COMMENT ON COLUMN text.news_providers.author IS
  'The origin''s author when the downloader captured one: zhihu answer '
  'AuthorName, gov.cn 来源. EMPTY STRING = the provider-level row (no '
  'distinct author — ndrc/pboc/ai_daily rows and gov CSV rows). Sentinel '
  'instead of NULL because the PK (source, author) cannot hold NULL; same '
  'ORIGIN concept as the fake-news detector (author when captured, else '
  'source).';

COMMENT ON COLUMN text.news_providers.is_blocked IS
  'Blocklist flag for the origin: true = flagged as a blocked/mistrusted '
  'provider or author. MANUAL curation — the registry sync''s upsert '
  'never touches this column (it updates only the seen-date span), so '
  'flipping it here survives every sync. No consumer filters on it yet.';

COMMENT ON COLUMN text.news_providers.first_seen_date IS
  'Earliest text.news.date among the origin''s articles (full-history '
  'MIN, recomputed by each registry sync).';

COMMENT ON COLUMN text.news_providers.last_seen_date IS
  'Latest text.news.date among the origin''s articles (full-history MAX,
  recomputed by each registry sync).';

COMMENT ON TABLE text.news_providers IS
  'News origin registry: one row per (source, author) — provider-level '
  'rows carry author = ''''. Holds the manual is_blocked blocklist flag '
  'plus the origin''s seen-date span, synced from text.news by '
  'analysis.news_audit (upsert preserves is_blocked).';
