
-- ============================================================================
--  Per-article comments (text.news_comments).
--  Populated by builds.text from downloaded artifacts — zhihu only for now
--  (root comments + their embedded replies of each answer/article, fetched
--  by downloads.macro.zhihu.news --with-comments). PK (source, comment_id):
--  the platform comment id is the natural key, so re-loading an artifact is
--  idempotent and the load path checks these PKs before writing.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Reader comments on corpus articles — the discussion thread around each piece.
-- tags: comments, 评论, news, 新闻, discussion, 讨论

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
    search_tsv   TSVECTOR,                    -- weighted full-text search vector: content C

    CONSTRAINT pk_news_comments PRIMARY KEY (source, comment_id),
    CONSTRAINT fk_news_comments_news FOREIGN KEY (news_id)
        REFERENCES text.news (news_id)
);

CREATE INDEX IF NOT EXISTS ix_news_comments_news_id
    ON text.news_comments (news_id);

-- Weighted full-text search — same contract as
-- text.news.search_tsv (the query must be segmented the same way).
CREATE INDEX IF NOT EXISTS ix_news_comments_search_tsv_gin
    ON text.news_comments USING gin (search_tsv);

COMMENT ON TABLE text.news_comments IS
  'Reader comments per article. Loaded by builds.text (zhihu only for now): '
  'root comments + embedded replies fetched by downloads.macro.zhihu.news '
  '--with-comments. news_id joins the parent article in text.news.';

COMMENT ON COLUMN text.news_comments.search_tsv IS
  'Weighted search tsvector (simple config over segmented text): content C. '
  'Same contract as text.news.search_tsv.';
