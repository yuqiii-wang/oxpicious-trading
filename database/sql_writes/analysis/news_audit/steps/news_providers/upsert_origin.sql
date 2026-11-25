-- SQL write action — text.news_providers upsert (one origin).
-- Executed by analysis/news_audit/steps.py (ProviderRegistryStep) via
-- executemany over the distinct origins of text.news.
--
-- Params (asyncpg $n, in order):
--   $1 source           TEXT — provider (text.news.source)
--   $2 author           TEXT — '' = provider-level row
--   $3 first_seen_date  DATE — MIN(text.news.date) over the origin
--   $4 last_seen_date   DATE — MAX(text.news.date) over the origin
--   $5 news_type        SMALLINT — label_dim 'news_type' code, classified
--                         from the source by config.classify_source_type
--
-- The conflict arm updates the DERIVED columns (seen-date span +
-- news_type) + updated_at — the manual is_blocked flag is deliberately
-- never touched by the sync.
INSERT INTO text.news_providers
    (source, author, first_seen_date, last_seen_date, news_type)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (source, author) DO UPDATE SET
    news_type       = EXCLUDED.news_type,
    first_seen_date = EXCLUDED.first_seen_date,
    last_seen_date  = EXCLUDED.last_seen_date,
    updated_at      = now()
