-- ============================================================================
--  LLM Q&A corpus
--  Tables: text.qa_llm_info, text.qa_pairs, text.qa_embeddings, text.qa_sessions
--    text.qa_llm_info   — per-generation LLM config (system prompt + model).
--                         Created first: text.qa_pairs has an FK to it.
--    text.qa_pairs      — curated question/answer documents (the knowledge
--                         base the LLM retrieves from), each linked to the
--                         text.news_groups reference set it was generated
--                         from.
--    text.qa_embeddings — embedding of the question (or concatenated q+a)
--                         for RAG similarity search (NOT IN USE yet).
--    text.qa_sessions   — optional logged Q&A turns (user question + chosen
--                         answer id + model) for evaluation / feedback.
--
--  Embedding dims = 1536 for now; revisit with the embedding model choice.
--  hnsw cosine index for ANN retrieval — query with the <=> operator.
-- ============================================================================

CREATE TABLE IF NOT EXISTS text.qa_llm_info (
    qa_llm_info_id  SERIAL PRIMARY KEY,
    system_prompt   TEXT,
    llm_model       TEXT,
    language        TEXT        DEFAULT 'zh',
    is_active       BOOLEAN     NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE text.qa_llm_info IS
  'LLM generation config: system prompt + model used to produce qa_pairs. '
  'One row per generation setup; qa_pairs.qa_llm_info_id points here.';

CREATE TABLE IF NOT EXISTS text.qa_pairs (
    qa_id          BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question       TEXT        NOT NULL,
    answer         TEXT        NOT NULL,
    context        TEXT,                       -- optional supporting passage
    category       TEXT,                       -- null for now
    industry_id    TEXT,
    news_group_id  INTEGER,                    -- reference set (see news_group_items)
    qa_llm_info_id INTEGER,
    language       TEXT        DEFAULT 'zh',
    is_active      BOOLEAN     NOT NULL DEFAULT true,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_qa_pairs UNIQUE (question, news_group_id),
    CONSTRAINT fk_qa_pairs_news_groups FOREIGN KEY (news_group_id)
        REFERENCES text.news_groups (news_group_id),
    CONSTRAINT fk_qa_pairs_qa_llm_info FOREIGN KEY (qa_llm_info_id)
        REFERENCES text.qa_llm_info (qa_llm_info_id)
);

COMMENT ON TABLE text.qa_pairs IS
  'Curated Q&A knowledge base for LLM retrieval. One row per question/answer '
  'pair, with news-group provenance (text.news_groups -> text.news_group_items) '
  'and optional industry linkage.';

-- ============================================================================
--  *** NOT IN USE — no embedding pipeline yet ***
--  Created empty for forward-compatibility. Revisit dims (1536 = OpenAI
--  3-small, 1024 = BAAI/bge-m3 local) when the pipeline is built — the
--  table will still be empty, so drop/recreate is cheap.
-- ============================================================================
CREATE TABLE IF NOT EXISTS text.qa_embeddings (
    qa_id       BIGINT      NOT NULL,
    embedding   vector(1536),              -- question (or q+a) embedding
    model       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_qa_embeddings PRIMARY KEY (qa_id),
    CONSTRAINT fk_qa_embeddings_qa FOREIGN KEY (qa_id)
        REFERENCES text.qa_pairs (qa_id)
);

COMMENT ON TABLE text.qa_embeddings IS
  'Embedding of each qa_pairs row for cosine-similarity retrieval (RAG). '
  'Stored separately so the heavy embedding column does not bloat the base '
  'table. NOT IN USE yet.';

CREATE INDEX IF NOT EXISTS ix_qa_embeddings_cosine
    ON text.qa_embeddings
    USING hnsw (embedding vector_cosine_ops);

-- Optional logged Q&A turns for evaluation / feedback loop.
CREATE TABLE IF NOT EXISTS text.qa_sessions (
    session_id    BIGSERIAL   PRIMARY KEY,
    qa_id         BIGINT,                      -- answer chosen / retrieved
    user_question TEXT        NOT NULL,
    model         TEXT,
    score         NUMERIC(4,3),                -- optional relevance score 0..1
    feedback      SMALLINT,                    -- user thumbs: -1 / 0 / 1
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fk_qa_sessions_qa FOREIGN KEY (qa_id)
        REFERENCES text.qa_pairs (qa_id)
);

COMMENT ON TABLE text.qa_sessions IS
  'Logged LLM Q&A turns: the user question, the retrieved/answered qa_id, model, '
  'and optional relevance score / user feedback for eval and improvement.';
