

-- ============================================================================
--  *** NOT IN USE — no embedding pipeline yet ***
--  Created empty for forward-compatibility. If the local BAAI/bge-m3 model
--  (1024 dims) is adopted, drop and recreate this still-empty table with
--  vector(1024) — no data migration needed.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Per-article content embeddings — reserved for the semantic-search pipeline.
-- tags: embeddings, 向量, semantic search, 语义检索

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
