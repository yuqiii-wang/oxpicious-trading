-- ============================================================================
--  Master Import Script — text/ (regenerated 2026-11-10: ONE TABLE = ONE
--  FILE; complete + FK-safe order; supersedes the stale hand list).
--  Usage: psql -d analytics -f text/00_init.sql
-- ============================================================================

-- schema files
\ir 00_text_schema.sql

-- tables (FK order)
\ir news.sql
\ir news_providers.sql
\ir news_groups.sql
\ir search_lexicon_df.sql
\ir news_comments.sql
\ir news_digestions.sql
\ir news_embeddings.sql
\ir news_group_items.sql
\ir news_keywords.sql
\ir news_provider_audit.sql
\ir news_content_audit.sql
\ir news_coverage_audit.sql
\ir news_lexicon_scores.sql
\ir news_text_daily.sql

-- indexes
\ir 13_news_keyword_audit.sql

