-- ============================================================================
--  News providers registry
--  Table: text.news_providers — one row per news ORIGIN (source, author):
--  the provider-level row (author = '') plus one row per captured author
--  (zhihu answer authors, gov 来源). Carries the origin's media type
--  (news_type — social media / financial report / news article / …),
--  the blocklist flag (is_blocked) and the origin's seen-date span,
--  synced from text.news by analysis.news_audit.
--
--  Column naming: "provider" here is text.news.source (gov, zhihu, ndrc,
--  pboc_*, ai_daily) — the repo's news vocabulary calls it source, so the
--  column keeps that name to join/filter text.news and the news API's
--  source filters directly. author uses '' (NOT NULL) as the "no distinct
--  author" sentinel — the same ORIGIN concept the fake-news detector uses
--  (author when captured, else source); PG primary keys cannot hold NULL.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The news origin registry — one row per provider/author pair with the origin's media type, the blocklist flag (auto-set on more than one audited fake item) and the seen-date span.
-- tags: provider, 提供方, author, 作者, registry, 名录, blocklist, 黑名单, news, 新闻, type, 类型, social media, 社交媒体


CREATE TABLE IF NOT EXISTS text.news_providers (
    source           TEXT        NOT NULL,             -- provider: text.news.source (gov, zhihu, ndrc, ...)
    author           TEXT        NOT NULL DEFAULT '',  -- '' = provider-level row (no distinct author)
    news_type        SMALLINT    NOT NULL DEFAULT 99,  -- the origin's media type (label_dim 'news_type'): 0 news_article | 1 financial_report | 2 social_media | 3 ai_digest | 99 other
    is_blocked       BOOLEAN     NOT NULL DEFAULT false,   -- blocklist flag: auto-set on >1 audited fake item; manual curation otherwise
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

COMMENT ON COLUMN text.news_providers.news_type IS
  'The origin''s MEDIA type — label_dim domain ''news_type'': 0 '
  'news_article (news portals + official news items — gov, ndrc and the '
  'web-scraped portals, the unmapped default), 1 financial_report '
  '(official monetary-policy / financial data releases: pboc_lpr, '
  'pboc_omo, pboc_oma), 2 social_media (UGC platforms: zhihu, xueqiu, '
  'weibo, guba/caifuhao, 淘股吧, 韭研公社, bilibili, the broker '
  'communities), 3 ai_digest (the repo''s own ai_daily), 99 other '
  '(unrecognized). Derived from text.news.source by the registry sync''s '
  'classify (analysis/news_audit/config.py — curated platform list + '
  'substring guards) and REWRITTEN by every sync (sync-owned, unlike the '
  'manual is_blocked); read against the audit metrics — a fake share '
  'concentrated in social_media origins is the expected shape, the same '
  'share in financial_report is the alarm.';

COMMENT ON COLUMN text.news_providers.is_blocked IS
  'Blocklist flag for the origin: true = blocked — the news API''s '
  'default feeds exclude the origin''s rows (include_blocked opts back '
  'in). TWO writers: the news_audit pipeline AUTO-blocks an origin once '
  'its audit history carries more than one fake item '
  '(SUM(text.news_provider_audit.num_fake) > 1 — one-way: a manual '
  'unblock re-blocks on the next audit run; '
  'analysis/news_audit/steps/news_providers/block_fake_origins.sql), and '
  'manual curation handles everything else. The registry sync''s upsert '
  'still never touches this column (it updates only the seen-date span '
  '+ news_type).';

COMMENT ON COLUMN text.news_providers.first_seen_date IS
  'Earliest text.news.date among the origin''s articles (full-history '
  'MIN, recomputed by each registry sync).';

COMMENT ON COLUMN text.news_providers.last_seen_date IS
  'Latest text.news.date among the origin''s articles (full-history MAX,
  recomputed by each registry sync).';

COMMENT ON TABLE text.news_providers IS
  'News origin registry: one row per (source, author) — provider-level '
  'rows carry author = ''''. Holds the origin''s media type (news_type), '
  'the is_blocked blocklist flag (auto-set by the news_audit pipeline on '
  'more than one audited fake item, manual curation otherwise) plus the '
  'origin''s seen-date span, synced from text.news by analysis.news_audit '
  '(upsert preserves is_blocked, rewrites news_type from the source '
  'classify).';
