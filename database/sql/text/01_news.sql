-- ============================================================================
--  News + extracted keywords
--  Tables: text.news, text.news_groups, text.news_group_items,
--          text.news_keywords, text.news_embeddings
--    text.news             — one raw article per (title, source, date).
--    text.news_groups      — reference-set entity: a group of news articles
--                            cited together (e.g. the sources behind one AI
--                            QA answer). Membership lives in the junction.
--    text.news_group_items — junction: which articles belong to which group.
--    text.news_keywords    — per-article keyword frequency (FK to news).
--    text.news_embeddings  — per-article content embedding (NOT IN USE yet).
--
--  Embedding dims = 1536 (OpenAI text-embedding-3-small) for now; revisit
--  (1024 = BAAI/bge-m3) when the embedding pipeline is built.
-- ============================================================================

CREATE TABLE IF NOT EXISTS text.news (
    title       TEXT    NOT NULL,
    content     TEXT,
    date        DATE    NOT NULL,
    source      TEXT,                       -- gov, zhihu, ndrc, ...
    url         TEXT,
    industry_id TEXT,
    word_count  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news PRIMARY KEY (title, source, date)
);

COMMENT ON TABLE text.news IS
  'Raw news articles. One row per (title, source, date). content holds the '
  'article body; industry_id links to the industry taxonomy when applicable.';

-- Reference set: the group of news articles an AI QA answer was based on.
-- Plain entity table — deliberately NOT INHERITS (text.news): table
-- inheritance would leak news_groups rows into SELECT * FROM text.news.
CREATE TABLE IF NOT EXISTS text.news_groups (
    news_group_id SERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE text.news_groups IS
  'Reference-set entity: a group of news articles cited together (e.g. by '
  'one AI QA answer). Membership is in text.news_group_items.';

-- Junction: group membership. One group may cite many articles; one article
-- may be cited by many groups.
CREATE TABLE IF NOT EXISTS text.news_group_items (
    news_group_id INTEGER     NOT NULL,
    news_title    TEXT        NOT NULL,
    source        TEXT        NOT NULL,
    date          DATE        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_group_items
        PRIMARY KEY (news_group_id, news_title, source, date),
    CONSTRAINT fk_news_group_items_group FOREIGN KEY (news_group_id)
        REFERENCES text.news_groups (news_group_id),
    CONSTRAINT fk_news_group_items_news FOREIGN KEY (news_title, source, date)
        REFERENCES text.news (title, source, date)
);

COMMENT ON TABLE text.news_group_items IS
  'Junction between text.news_groups and text.news: the articles each group '
  'cites. PK keeps membership idempotent on re-insert.';

CREATE TABLE IF NOT EXISTS text.news_keywords (
    news_title  TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    date        DATE    NOT NULL,           -- denormalized for join convenience
    keyword     TEXT    NOT NULL,
    count       INTEGER,                     -- raw count in the article
    count_pct   NUMERIC(6,4),                -- count / word_count
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_keywords
        PRIMARY KEY (news_title, source, date, keyword),
    CONSTRAINT fk_news_keywords_news FOREIGN KEY (news_title, source, date)
        REFERENCES text.news (title, source, date)
);

COMMENT ON TABLE text.news_keywords IS
  'Per-article keyword frequency extracted from text.news. count = occurrences; '
  'count_pct = count / word_count. Used for keyword-level sentiment/topic search.';

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
