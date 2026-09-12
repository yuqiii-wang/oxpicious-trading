-- ============================================================================
--  News + extracted keywords
--  Tables: text.news, text.news_groups, text.news_group_items,
--          text.news_keywords, text.news_embeddings
--    text.news             — one raw article per (title, source, date), with
--                            a surrogate news_id (identity) other tables map
--                            to. Search is implemented in Python, not SQL.
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

CREATE TABLE IF NOT EXISTS text.news (
    news_id       BIGINT    GENERATED ALWAYS AS IDENTITY,
    title         TEXT      NOT NULL,
    content       TEXT,
    date          DATE      NOT NULL,
    source        TEXT,                       -- gov, zhihu, ndrc, ...
    url           TEXT,
    author        TEXT,                       -- zhihu answer author; gov 来源; NULL when unknown
    industry_id   TEXT,
    word_count    INTEGER,
    votes         INTEGER,                    -- upvotes when the source provides them (zhihu VoteUpCount); NULL otherwise
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news PRIMARY KEY (title, source, date),
    CONSTRAINT uq_news_news_id UNIQUE (news_id)
);

-- Upgrade for deployments created before the votes column existed.
ALTER TABLE text.news ADD COLUMN IF NOT EXISTS votes INTEGER;


-- Uniqueness as a standalone index (not just the table constraint) so it is
-- also created on deployments where the table already existed without
-- news_id. The keyword index's news_ids mapping relies on this.
CREATE UNIQUE INDEX IF NOT EXISTS uq_news_news_id ON text.news (news_id);

-- Filter indexes backing the UI nav (source / author / industry + date
-- windows). All are filter columns of text.news list/calendar queries; date
-- DESC keeps ORDER BY date DESC walks index-ordered.
CREATE INDEX IF NOT EXISTS ix_news_source_date ON text.news (source, date DESC);
CREATE INDEX IF NOT EXISTS ix_news_industry_date ON text.news (industry_id, date DESC);
CREATE INDEX IF NOT EXISTS ix_news_author_date ON text.news (author, date DESC);
CREATE INDEX IF NOT EXISTS ix_news_date ON text.news (date DESC);
-- today_industry_change refresh joins (industry_id, date) per touched article.
CREATE INDEX IF NOT EXISTS ix_news_keywords_industry_date
    ON text.news_keywords (industry_id, date);

COMMENT ON COLUMN text.news.news_id IS
  'Surrogate id (identity). text.news_keywords maps (keyword, news_id), so '
  'keyword search resolves articles by id without the (title, source, date) '
  'natural-key triple.';

COMMENT ON COLUMN text.news.author IS
  'Author/origin when the downloader captured one: zhihu answer AuthorName, '
  'gov.cn 来源 (parsed from date_raw). NULL for ndrc/pboc (source is the org).';

COMMENT ON TABLE text.news IS
  'Raw news articles. One row per (title, source, date). content holds the '
  'article body; industry_id links to the industry taxonomy when applicable.';

-- ============================================================================
--  Per-article comments (text.news_comments).
--  Populated by builds.text from downloaded artifacts — zhihu only for now
--  (root comments + their embedded replies of each answer/article, fetched
--  by downloads.macro.zhihu.news --with-comments). PK (source, comment_id):
--  the platform comment id is the natural key, so re-loading an artifact is
--  idempotent and the load path checks these PKs before writing.
-- ============================================================================
CREATE TABLE IF NOT EXISTS text.news_comments (
    comment_id   BIGINT      NOT NULL,       -- platform comment id (zhihu comment id)
    source       TEXT        NOT NULL,       -- 'zhihu' for now
    parent_comment_id BIGINT,                -- root comment id for nested replies; NULL for roots
    news_id      BIGINT      NOT NULL,       -- parent article (text.news)
    author       TEXT,                       -- NULL when the platform hides it
    content      TEXT,
    date         DATE,                       -- comment created date (Asia/Shanghai)
    votes        INTEGER,                    -- comment like count; NULL when not provided
    is_reply     BOOLEAN       NOT NULL DEFAULT false,  -- true for nested replies
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_comments PRIMARY KEY (source, comment_id),
    CONSTRAINT fk_news_comments_news FOREIGN KEY (news_id)
        REFERENCES text.news (news_id)
);

CREATE INDEX IF NOT EXISTS ix_news_comments_news_id
    ON text.news_comments (news_id);

-- Upgrade for deployments created before threading existed.
ALTER TABLE text.news_comments ADD COLUMN IF NOT EXISTS parent_comment_id BIGINT;

COMMENT ON TABLE text.news_comments IS
  'Reader comments per article. Loaded by builds.text (zhihu only for now): '
  'root comments + embedded replies fetched by downloads.macro.zhihu.news '
  '--with-comments. news_id joins the parent article in text.news.';

-- Reference set: the group of news articles an AI QA answer was based on.
-- Plain entity table — deliberately NOT INHERITS (text.news): table
-- inheritance would leak news_groups rows into SELECT * FROM text.news.
CREATE TABLE IF NOT EXISTS text.news_groups (
    news_group_id SERIAL PRIMARY KEY,
    news_group_name TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE text.news_groups IS
  'Reference-set entity: a group of news articles cited together (e.g. by '
  'one AI QA answer). Membership is in text.news_group_items.';

-- Junction: group membership. One group may cite many articles; one article
-- may be cited by many groups.
CREATE TABLE IF NOT EXISTS text.news_group_items (
    news_group_id INTEGER     NOT NULL,
    news_id       BIGINT      NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_group_items PRIMARY KEY (news_group_id, news_id),
    CONSTRAINT fk_news_group_items_news_groups FOREIGN KEY (news_group_id)
        REFERENCES text.news_groups (news_group_id),
    CONSTRAINT fk_news_group_items_news FOREIGN KEY (news_id)
        REFERENCES text.news (news_id)
);

COMMENT ON TABLE text.news_group_items IS
  'Junction between text.news_groups and text.news: the articles each group '
  'cites. PK keeps membership idempotent on re-insert.';

-- ============================================================================
--  Per-article keyword frequencies + per-keyword corpus stats. PK is
--  (keyword, news_id): one row per article per keyword. The former
--  news_keyword_index table was folded in here: doc_freq / idf are
--  keyword-level stats duplicated onto EVERY row of that keyword (keyword is
--  not unique, the stats are constant within one keyword). The Python insert
--  pipeline recalculates them for all rows of each keyword it touches — and
--  for all keywords when the total article count changed, since IDF is
--  corpus-level. No SQL functions/triggers: the pipeline owns the writes.
-- ============================================================================
CREATE TABLE IF NOT EXISTS text.news_keywords (
    news_id               BIGINT      NOT NULL,
    keyword               TEXT        NOT NULL,
    date                  DATE        NOT NULL,  -- denormalized for join convenience
    industry_id           TEXT,                  -- denormalized from text.news
    today_industry_change DOUBLE PRECISION,      -- industry's change on this date or next date if today is holiday
    count                 INTEGER,               -- raw count in the article
    count_pct             NUMERIC(6,4),          -- count / article_word_count
    doc_freq              INTEGER,               -- # articles containing keyword
    idf                   NUMERIC(12,6),         -- ln(1 + N / doc_freq)
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_keywords PRIMARY KEY (keyword, news_id),
    CONSTRAINT fk_news_keywords_news FOREIGN KEY (news_id)
        REFERENCES text.news (news_id)
);


CREATE INDEX IF NOT EXISTS ix_news_keywords_news_id
    ON text.news_keywords (news_id);

COMMENT ON COLUMN text.news_keywords.doc_freq IS
  'Document frequency of the keyword: number of articles containing it. '
  'Constant within one keyword (duplicated on each of its rows); maintained '
  'by the Python insert pipeline.';

COMMENT ON COLUMN text.news_keywords.idf IS
  'Inverse document frequency ln(1 + N / doc_freq), N = total article count. '
  'Constant within one keyword (duplicated on each of its rows); higher = '
  'rarer / more significant keyword.';


-- ============================================================================
--  *** NOT IN USE — no embedding pipeline yet ***
--  Created empty for forward-compatibility. If the local BAAI/bge-m3 model
--  (1024 dims) is adopted, drop and recreate this still-empty table with
--  vector(1024) — no data migration needed.
-- ============================================================================
CREATE TABLE IF NOT EXISTS text.news_embeddings (
    news_title  TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    date        DATE    NOT NULL,
    embedding   vector(1536),               -- content embedding
    model       TEXT,                        -- embedding model id
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_embeddings PRIMARY KEY (news_title, source, date),
    CONSTRAINT fk_news_embeddings_news FOREIGN KEY (news_title, source, date)
        REFERENCES text.news (title, source, date)
);

COMMENT ON TABLE text.news_embeddings IS
  'Content embedding of each news article (vector(1536)) for cosine-similarity '
  'search (NOT IN USE yet). Join on text.news via (news_title, source, date).';

-- ANN index for cosine distance — query with the <=> operator.
CREATE INDEX IF NOT EXISTS ix_news_embeddings_cosine
    ON text.news_embeddings
    USING hnsw (embedding vector_cosine_ops);
