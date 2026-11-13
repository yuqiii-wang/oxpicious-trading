-- ============================================================================
--  News + extracted keywords
--  Tables: text.news, text.news_groups, text.news_group_items,
--          text.news_keywords, text.news_embeddings
--    text.news             — one raw article per (title, source, date), with
--                            a surrogate news_id (identity) other tables map
--                            to. Search is implemented in Python, not SQL:
--                            the keyword rows and the weighted search_tsv
--                            tsvector are both written by the pipeline.
--    text.news_groups      — reference-set entity: a group of news articles
--                            cited together (e.g. the sources behind one AI
--                            QA answer). Membership lives in the junction.
--    text.news_group_items — junction: which articles belong to which group.
--    text.news_keywords    — per-article keyword frequency, keyed by
--                            (keyword, news_id), carrying the per-keyword
--                            corpus stats (doc_freq, idf) on every row of
--                            that keyword. Recalculated by the Python insert
--                            pipeline whenever keywords are written.
--    text.news_embeddings  — per-article content embedding (NOT IN USE yet).
--
--  Embedding dims = 1536 (OpenAI text-embedding-3-small) for now; revisit
--  (1024 = BAAI/bge-m3) when the embedding pipeline is built.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The news corpus — one raw article per source per day, with industry tags and a full-text search vector.
-- tags: news, 新闻, corpus, 语料, articles, 文章, sentiment, 情绪, full-text search, 全文检索


CREATE TABLE IF NOT EXISTS text.news (
    news_id       BIGINT    GENERATED ALWAYS AS IDENTITY,
    title         TEXT      NOT NULL,
    content       TEXT,
    date          DATE      NOT NULL,
    source        TEXT,                       -- gov, zhihu, ndrc, ...
    url           TEXT,
    author        TEXT,                       -- zhihu answer author; gov 来源; NULL when unknown
    industry_id   TEXT,
    sector_id     TEXT,                       -- denormalized parent sector of industry_id
    word_count    INTEGER,
    votes         INTEGER,                    -- upvotes when the source provides them (zhihu VoteUpCount); NULL otherwise
    is_big_fluctuation BOOLEAN NOT NULL DEFAULT false,  -- mapped industry's day qualified as a big move (builds.text.big_market_mv)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_tsv    TSVECTOR,                   -- weighted full-text search vector: title A, content C

    CONSTRAINT pk_news PRIMARY KEY (title, source, date),
    CONSTRAINT uq_news_news_id UNIQUE (news_id)
);

-- Uniqueness as a standalone index (not just the table constraint) so it is
-- also created on deployments where the table already existed without
-- news_id. The keyword index's news_ids mapping relies on this.
CREATE UNIQUE INDEX IF NOT EXISTS uq_news_news_id ON text.news (news_id);

-- Filter indexes backing the UI nav (source / author / industry / sector +
-- date windows). All are filter columns of text.news list/calendar queries;
-- date DESC keeps ORDER BY date DESC walks index-ordered.
CREATE INDEX IF NOT EXISTS ix_news_source_date ON text.news (source, date DESC);
CREATE INDEX IF NOT EXISTS ix_news_industry_date ON text.news (industry_id, date DESC);
CREATE INDEX IF NOT EXISTS ix_news_sector_date ON text.news (sector_id, date DESC);
CREATE INDEX IF NOT EXISTS ix_news_author_date ON text.news (author, date DESC);
CREATE INDEX IF NOT EXISTS ix_news_date ON text.news (date DESC);

-- Weighted full-text search: 'simple' config over StructBERT-segmented
-- text, written by the pipeline at store time (set_search_tsv_async).
-- Query SEGMENTED the same way: search_tsv @@
-- websearch_to_tsquery('simple', tokenize_for_search(<query>)).
CREATE INDEX IF NOT EXISTS ix_news_search_tsv_gin
    ON text.news USING gin (search_tsv);

COMMENT ON COLUMN text.news.news_id IS
  'Surrogate id (identity). text.news_keywords maps (keyword, news_id), so '
  'keyword search resolves articles by id without the (title, source, date) '
  'natural-key triple.';

COMMENT ON COLUMN text.news.author IS
  'Author/origin when the downloader captured one: zhihu answer AuthorName, '
  'gov.cn 来源 (parsed from date_raw). NULL for ndrc/pboc (source is the org).';

COMMENT ON COLUMN text.news.sector_id IS
  'Parent sector of industry_id in the canonical taxonomy (FIN for BANKS, '
  'BROAD for BROAD_SSE, …), denormalized by the Python tagging pipeline so '
  'an L1-only scope filters on this column directly. Set iff industry_id '
  'is set; backfilled on the next builds.text run.';

COMMENT ON COLUMN text.news.is_big_fluctuation IS
  'True when the row''s mapped industry_id had a BIG market move on the '
  'row''s date: |single-day change| >= 3% AND Wilder RSI(3) of the '
  'industry composite (stats.industry_basic_stats mean_close, pool_size '
  '''all'') pinned at a rail (>= 97 up / <= 3 down). First qualifying day '
  'per industry survives; further qualifying days within 5 trading days '
  'of a surviving one stay false (cooldown). Maintained by the '
  'builds.text.big_market_mv full-recompute pass (re-runs are idempotent '
  'and re-derive the whole flag). Renamed from is_big_market_mv.';

COMMENT ON COLUMN text.news.search_tsv IS
  'Weighted search tsvector (simple config over StructBERT-segmented text): '
  'title A, content C, POSITIONS STRIPPED (strip() — GIN indexes lexemes '
  'only and ts_rank detoasting dominated the position payload; ranking '
  'keeps the A-D weights and matched-term count, loses within-document '
  'term frequency). Written by the pipeline (set_search_tsv_async) at '
  'store time; NULL rows are unindexed rows awaiting backfill. Weight map '
  '+ write contract: TSV_SPECS (_common/tokenization/search_indexing.py).';

COMMENT ON TABLE text.news IS
  'Raw news articles. One row per (title, source, date). content holds the '
  'article body; industry_id / sector_id link to the industry taxonomy when '
  'applicable.';
