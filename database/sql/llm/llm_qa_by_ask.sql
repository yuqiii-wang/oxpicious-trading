-- ============================================================================
--  LLM Q&A by ask — persisted interactive AI-ask history
--  Tables (schema text; canonical DDL, applied idempotently):
--    llm.llm_qa_by_ask             — one row per interactive chart ask
--                                     (question + answer + ask-time facts)
--    llm.llm_qa_ask_context        — 1:1 per ask: the chart context the
--                                     question was asked against (plotInfo)
--    llm.llm_qa_ask_context_images — junction: one ask context -> N images
--                                     in multi_media.src_images
--    llm.llm_qa_keywords_by_ask    — per-ask keyword rows (the keyword
--                                     search index, like text.news_keywords)
--
--  Written by llm_agents.llm_ask.storage (called from the llm_ask CLI for
--  every --payload-file ask, i.e. every data_viz "AI Ask" modal submit) —
--  unlike llm.llm_qa (the pipeline-generated knowledge base) these rows are
--  append-only user history: no natural unique key, the same question may be
--  legitimately re-asked. Reads: the AiPage "QA by Ask" feed via the Express
--  ai-ask-history service.
--
--  Data relationships:
--    llm_qa_by_ask (ask_id) 1:1 llm_qa_ask_context (ask_id PK/FK)
--      llm_qa_ask_context 1:N llm_qa_ask_context_images (ask_id, image_id)
--        llm_qa_ask_context_images N:1 multi_media.src_images (image_id)
--    llm_qa_by_ask 1:N llm_qa_keywords_by_ask (keyword, ask_id)
--
--  Search design (why the columns are where they are):
--    * code / product / ask_date — indexed directly on llm_qa_by_ask; the
--      scoped feed filters + orders without a join.
--    * free-text terms — hit question/answer ILIKE OR a keyword row
--      (llm_qa_keywords_by_ask PK (keyword, ask_id) serves exact/prefix
--      keyword lookups without a join); keyword rows denormalize code /
--      product / ask_day so "keywords for code X" is single-table.
--    * weighted full text — search_tsv: 'simple' tsvector over the
--      SEGMENTED question/answer, GIN-indexed, ranked by ts_rank
--      (question A, answer B) — additive to the ILIKE/keyword search above.
--    * industry / sector scope — llm_qa_ask_context.industry_id / sector_id
--      (indexed), joined on ask_id.
--    * images — reached only on detail fetches (junction -> src_images);
--      bytea never travels in list queries.
--
--  Apply AFTER database/sql/multi_media/{00_schema,01_src_images}.sql — the
--  llm_qa_ask_context_images junction foreign-keys multi_media.src_images.
-- ============================================================================

CREATE TABLE IF NOT EXISTS llm.llm_qa_by_ask (
    ask_id        BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question      TEXT        NOT NULL,
    answer        TEXT,                       -- NULL when status = 'failed'; capped 2000 chars at store time (08_llm_summary_caps.sql)
    status        TEXT        NOT NULL DEFAULT 'answered',
    error_tail    TEXT,                       -- failure stderr/exception tail when status = 'failed'; capped 2000 (08_llm_summary_caps.sql)
    online_search BOOLEAN     NOT NULL DEFAULT false,
    search_query  TEXT,                       -- the modal's search line (NULL when the question was searched)
    provider      TEXT,
    llm_model     TEXT,
    language      TEXT        DEFAULT 'zh',
    code          TEXT,                       -- first scope instrument code — the ask's primary subject
    product       TEXT,                       -- product/page identity tag (spec product, else page path)
    request_ids   BIGINT[]    NOT NULL DEFAULT '{}',  -- the ask's llm.llm_requests ids (source-log backlink)
    is_active     BOOLEAN     NOT NULL DEFAULT true,
    ask_date      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_tsv    TSVECTOR,                   -- weighted full-text search vector: question A, answer B

    CONSTRAINT ck_llm_qa_by_ask_status CHECK (status IN ('answered', 'failed'))
);

COMMENT ON COLUMN llm.llm_qa_by_ask.request_ids IS
  'The ask''s OWN LLM requests as llm.llm_requests ids, in flow order — '
  'one plain-completion ask, or search + compose + ask on the '
  'online-search path (the backlink into the source log; join '
  'llm.llm_requests to recover each request''s full payload, response, '
  'and context_ref_ids). Collected at persist time by '
  'llm_agents.llm_ask.storage (service + flow-window match on the '
  'chat-completions endpoint); empty for asks persisted before the log '
  'existed or when collection failed (non-fatal — the ask still stores).';

-- Code-scoped feeds: picked security -> its ask history, newest first.
CREATE INDEX IF NOT EXISTS ix_llm_qa_by_ask_code_date
    ON llm.llm_qa_by_ask (code, ask_date DESC);

-- Product-scoped feeds (one product page's ask history).
CREATE INDEX IF NOT EXISTS ix_llm_qa_by_ask_product_date
    ON llm.llm_qa_by_ask (product, ask_date DESC);

-- Full-history calendar mapping and ask_date-ordered feeds.
CREATE INDEX IF NOT EXISTS ix_llm_qa_by_ask_ask_date
    ON llm.llm_qa_by_ask (ask_date DESC);

-- Weighted full-text search — same contract as
-- text.news.search_tsv (the query must be segmented the same way).
CREATE INDEX IF NOT EXISTS ix_llm_qa_by_ask_search_tsv_gin
    ON llm.llm_qa_by_ask USING gin (search_tsv);

COMMENT ON COLUMN llm.llm_qa_by_ask.answer IS
  'The adviser answer text. NULL when status = ''failed'' (the ask errored '
  'before an answer existed); failure detail lives in error_tail.';

COMMENT ON COLUMN llm.llm_qa_by_ask.status IS
  'answered = the ask produced an answer; failed = it errored (provider '
  'failure, malformed payload, …) — persisted too so the history shows the '
  'gap instead of silently skipping it.';

COMMENT ON COLUMN llm.llm_qa_by_ask.search_query IS
  'The modal''s online-search line (seeded with chart title + date) when '
  'online_search is true; NULL when the question itself was searched.';

COMMENT ON COLUMN llm.llm_qa_by_ask.code IS
  'First scope instrument code from the payload plotInfo (the ask''s primary '
  'subject) — denormalized by llm_agents.llm_ask.storage so code-scoped '
  'feeds filter this column directly without touching the context row.';

COMMENT ON COLUMN llm.llm_qa_by_ask.product IS
  'Product identity tag for keyword search: the chart spec''s product id '
  'when the author set one, else the UI page path. Carried from data_viz in '
  'the plotInfo payload.';

COMMENT ON COLUMN llm.llm_qa_by_ask.ask_date IS
  'When the ask was made (now() at persist time) — the AI page maps it to '
  'the latest trading day on or before it for the calendar strip, same as '
  'llm.llm_qa.qa_date.';

COMMENT ON COLUMN llm.llm_qa_by_ask.search_tsv IS
  'Weighted search tsvector (simple config over segmented text): question A, '
  'answer B (failed asks have no answer — question only). Same contract as '
  'text.news.search_tsv.';

COMMENT ON TABLE llm.llm_qa_by_ask IS
  'Interactive AI-ask history: one row per data_viz "AI Ask" modal submit '
  '(question + answer + ask-time facts), written by llm_agents.llm_ask. '
  'Append-only — unlike llm.llm_qa (the curated knowledge base) there is '
  'no natural unique key. Chart context in llm.llm_qa_ask_context, images '
  'in multi_media.src_images via llm.llm_qa_ask_context_images, keyword '
  'index in llm.llm_qa_keywords_by_ask.';
