
-- ----------------------------------------------------------------------------
--  Per-ask keywords — the keyword search index (text.news_keywords analog).
--  Derived deterministically at persist time by llm_agents.llm_ask.storage
--  from the payload: instrument codes/names, product/page, in-plot state
--  values + searchKeywords, series names. No LLM call, no segmentation.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS llm.llm_qa_keywords_by_ask (
    ask_id     BIGINT      NOT NULL,
    keyword    TEXT        NOT NULL,
    kind       TEXT        NOT NULL,
    code       TEXT,                       -- denormalized from the ask row
    product    TEXT,                       -- denormalized from the ask row
    ask_day    DATE,                       -- denormalized Asia/Shanghai day of ask_date
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_llm_qa_keywords_by_ask PRIMARY KEY (keyword, ask_id),
    CONSTRAINT fk_llm_qa_keywords_by_ask_ask FOREIGN KEY (ask_id)
        REFERENCES llm.llm_qa_by_ask (ask_id) ON DELETE CASCADE,
    CONSTRAINT ck_llm_qa_keywords_by_ask_kind CHECK (
        kind IN ('code', 'name', 'product', 'item', 'series'))
);

-- Code-scoped keyword browsing (the security's ask-history keywords).
CREATE INDEX IF NOT EXISTS ix_llm_qa_keywords_by_ask_code_day
    ON llm.llm_qa_keywords_by_ask (code, ask_day DESC);

COMMENT ON COLUMN llm.llm_qa_keywords_by_ask.kind IS
  'Where the keyword came from: code = instrument code; name = instrument '
  'display name; product = product id / page path; item = in-plot state '
  'value or searchKeyword (the active toggles/indicators); series = plotted '
  'series name.';

COMMENT ON COLUMN llm.llm_qa_keywords_by_ask.ask_day IS
  'ask_date bucketed to an Asia/Shanghai day, denormalized so date-scoped '
  'keyword queries stay single-table (same denormalization pattern as '
  'text.news_keywords.date).';

COMMENT ON TABLE llm.llm_qa_keywords_by_ask IS
  'Keyword index for interactive AI asks (text.news_keywords analog): one '
  'row per (keyword, ask_id), derived deterministically from the ask '
  'payload at persist time. The AiPage by-ask search hits this table OR '
  'question/answer ILIKE; code/product/ask_day are denormalized for '
  'single-table scoped lookups.';
