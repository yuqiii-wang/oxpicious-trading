-- ============================================================================
--  LLM Q&A knowledge base
--  Table: text.llm_qa — one row per curated question/answer document (the
--  knowledge base the LLM retrieves from), with news-group provenance
--  (text.news_groups -> text.news_group_items) and the model that produced
--  the answer.
-- ============================================================================

CREATE TABLE IF NOT EXISTS text.llm_qa (
    qa_id          BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question       TEXT        NOT NULL,
    answer         TEXT        NOT NULL,
    context        TEXT,                       -- optional supporting passage
    category       TEXT,                       -- null for now
    industry_id    TEXT,
    news_group_id  INTEGER,                    -- reference set (see news_group_items)
    llm_model      TEXT,
    language       TEXT        DEFAULT 'zh',
    is_active      BOOLEAN     NOT NULL DEFAULT true,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_llm_qa UNIQUE (question, news_group_id),
    CONSTRAINT fk_llm_qa_news_groups FOREIGN KEY (news_group_id)
        REFERENCES text.news_groups (news_group_id)
);

COMMENT ON TABLE text.llm_qa IS
  'Curated Q&A knowledge base for LLM retrieval. One row per question/answer '
  'pair, with news-group provenance (text.news_groups -> text.news_group_items), '
  'optional industry linkage, and the model that generated the answer.';
