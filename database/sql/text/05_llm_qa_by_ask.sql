-- ============================================================================
--  LLM Q&A by ask — persisted interactive AI-ask history
--  Tables (schema text; canonical DDL, applied idempotently):
--    text.llm_qa_by_ask             — one row per interactive chart ask
--                                     (question + answer + ask-time facts)
--    text.llm_qa_ask_context        — 1:1 per ask: the chart context the
--                                     question was asked against (plotInfo)
--    text.llm_qa_ask_context_images — junction: one ask context -> N images
--                                     in multi_media.src_images
--    text.llm_qa_keywords_by_ask    — per-ask keyword rows (the keyword
--                                     search index, like text.news_keywords)
--
--  Written by llm_agents.llm_ask.storage (called from the llm_ask CLI for
--  every --payload-file ask, i.e. every data_viz "AI Ask" modal submit) —
--  unlike text.llm_qa (the pipeline-generated knowledge base) these rows are
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
--    * industry / sector scope — llm_qa_ask_context.industry_id / sector_id
--      (indexed), joined on ask_id.
--    * images — reached only on detail fetches (junction -> src_images);
--      bytea never travels in list queries.
--
--  Apply AFTER database/sql/multi_media/{00_schema,01_src_images}.sql — the
--  llm_qa_ask_context_images junction foreign-keys multi_media.src_images.
-- ============================================================================

CREATE TABLE IF NOT EXISTS text.llm_qa_by_ask (
    ask_id        BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question      TEXT        NOT NULL,
    answer        TEXT,                       -- NULL when status = 'failed'
    status        TEXT        NOT NULL DEFAULT 'answered',
    error_tail    TEXT,                       -- failure stderr/exception tail when status = 'failed'
    online_search BOOLEAN     NOT NULL DEFAULT false,
    search_query  TEXT,                       -- the modal's search line (NULL when the question was searched)
    provider      TEXT,
    llm_model     TEXT,
    language      TEXT        DEFAULT 'zh',
    code          TEXT,                       -- first scope instrument code — the ask's primary subject
    product       TEXT,                       -- product/page identity tag (spec product, else page path)
    is_active     BOOLEAN     NOT NULL DEFAULT true,
    ask_date      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_llm_qa_by_ask_status CHECK (status IN ('answered', 'failed'))
);

-- Code-scoped feeds: picked security -> its ask history, newest first.
CREATE INDEX IF NOT EXISTS ix_llm_qa_by_ask_code_date
    ON text.llm_qa_by_ask (code, ask_date DESC);

-- Product-scoped feeds (one product page's ask history).
CREATE INDEX IF NOT EXISTS ix_llm_qa_by_ask_product_date
    ON text.llm_qa_by_ask (product, ask_date DESC);

-- Full-history calendar mapping and ask_date-ordered feeds.
CREATE INDEX IF NOT EXISTS ix_llm_qa_by_ask_ask_date
    ON text.llm_qa_by_ask (ask_date DESC);

COMMENT ON COLUMN text.llm_qa_by_ask.answer IS
  'The adviser answer text. NULL when status = ''failed'' (the ask errored '
  'before an answer existed); failure detail lives in error_tail.';

COMMENT ON COLUMN text.llm_qa_by_ask.status IS
  'answered = the ask produced an answer; failed = it errored (provider '
  'failure, malformed payload, …) — persisted too so the history shows the '
  'gap instead of silently skipping it.';

COMMENT ON COLUMN text.llm_qa_by_ask.search_query IS
  'The modal''s online-search line (seeded with chart title + date) when '
  'online_search is true; NULL when the question itself was searched.';

COMMENT ON COLUMN text.llm_qa_by_ask.code IS
  'First scope instrument code from the payload plotInfo (the ask''s primary '
  'subject) — denormalized by llm_agents.llm_ask.storage so code-scoped '
  'feeds filter this column directly without touching the context row.';

COMMENT ON COLUMN text.llm_qa_by_ask.product IS
  'Product identity tag for keyword search: the chart spec''s product id '
  'when the author set one, else the UI page path. Carried from data_viz in '
  'the plotInfo payload.';

COMMENT ON COLUMN text.llm_qa_by_ask.ask_date IS
  'When the ask was made (now() at persist time) — the AI page maps it to '
  'the latest trading day on or before it for the calendar strip, same as '
  'text.llm_qa.qa_date.';

COMMENT ON TABLE text.llm_qa_by_ask IS
  'Interactive AI-ask history: one row per data_viz "AI Ask" modal submit '
  '(question + answer + ask-time facts), written by llm_agents.llm_ask. '
  'Append-only — unlike text.llm_qa (the curated knowledge base) there is '
  'no natural unique key. Chart context in text.llm_qa_ask_context, images '
  'in multi_media.src_images via text.llm_qa_ask_context_images, keyword '
  'index in text.llm_qa_keywords_by_ask.';

-- ----------------------------------------------------------------------------
--  Ask context — the chart the question was asked against (1:1 per ask).
--  Hot filter columns are extracted; the verbatim plotInfo payload is kept
--  as JSONB because series/state shapes vary per product page.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS text.llm_qa_ask_context (
    ask_id        BIGINT      NOT NULL,
    chart_kind    TEXT,
    chart_title   TEXT,
    chart_subtitle TEXT,
    chart_intro   TEXT,
    industry_id   TEXT,
    sector_id     TEXT,                       -- scope sector when the payload carried one
    window_start  TEXT,
    window_end    TEXT,
    granularity   TEXT,
    page          TEXT,                       -- UI route path the ask was submitted from
    theme_mode    TEXT,                       -- light / dark (the screenshot's theme)
    plot_info     JSONB       NOT NULL,       -- verbatim payload plotInfo (chart/scope/series/state/…)
    n_images      SMALLINT    NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_llm_qa_ask_context PRIMARY KEY (ask_id),
    CONSTRAINT fk_llm_qa_ask_context_ask FOREIGN KEY (ask_id)
        REFERENCES text.llm_qa_by_ask (ask_id) ON DELETE CASCADE
);

-- Industry-scope filter for the AI page's L2 picks (via the context join).
CREATE INDEX IF NOT EXISTS ix_llm_qa_ask_context_industry
    ON text.llm_qa_ask_context (industry_id);

-- Sector-scope filter for the AI page's L1-only picks.
CREATE INDEX IF NOT EXISTS ix_llm_qa_ask_context_sector
    ON text.llm_qa_ask_context (sector_id);

COMMENT ON COLUMN text.llm_qa_ask_context.plot_info IS
  'The verbatim plotInfo object POSTed by the AI Ask modal (chart identity, '
  'scope.instruments, window, series stats, in-plot state, searchKeywords) '
  '— series/state shapes vary per product page, so only the hot filter '
  'columns are extracted into real columns.';

COMMENT ON COLUMN text.llm_qa_ask_context.n_images IS
  'Number of screenshot rows linked through llm_qa_ask_context_images '
  '(the payload screenshot count after the size/count caps).';

COMMENT ON TABLE text.llm_qa_ask_context IS
  'Chart context of one interactive AI ask (1:1 with text.llm_qa_by_ask): '
  'extracted filter columns + the verbatim plotInfo JSONB. One context can '
  'reference N images through text.llm_qa_ask_context_images.';

-- ----------------------------------------------------------------------------
--  Ask-context -> image junction: one context entry, many images
--  (the modal attaches up to 4 stacked-chart screenshots per ask).
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS text.llm_qa_ask_context_images (
    ask_id   BIGINT   NOT NULL,
    image_id BIGINT   NOT NULL,
    position SMALLINT NOT NULL DEFAULT 0,     -- screenshot slot order in the payload

    CONSTRAINT pk_llm_qa_ask_context_images PRIMARY KEY (ask_id, image_id),
    CONSTRAINT fk_llm_qa_ask_context_images_ctx FOREIGN KEY (ask_id)
        REFERENCES text.llm_qa_ask_context (ask_id) ON DELETE CASCADE,
    CONSTRAINT fk_llm_qa_ask_context_images_img FOREIGN KEY (image_id)
        REFERENCES multi_media.src_images (image_id) ON DELETE CASCADE
);

-- Reverse lookup: where (which asks) one stored image is referenced.
CREATE INDEX IF NOT EXISTS ix_llm_qa_ask_context_images_image
    ON text.llm_qa_ask_context_images (image_id);

COMMENT ON COLUMN text.llm_qa_ask_context_images.position IS
  'Screenshot slot order within the ask (0-based, the payload array order) '
  '— the detail view re-attaches thumbnails in this order.';

COMMENT ON TABLE text.llm_qa_ask_context_images IS
  'Junction: which multi_media.src_images belong to which ask context. One '
  'context entry maps to N images (the modal''s stacked-chart screenshots, '
  'capped at 4); identical screenshots across asks share one src_images row '
  '(deduplicated by content_hash).';

-- ----------------------------------------------------------------------------
--  Per-ask keywords — the keyword search index (text.news_keywords analog).
--  Derived deterministically at persist time by llm_agents.llm_ask.storage
--  from the payload: instrument codes/names, product/page, in-plot state
--  values + searchKeywords, series names. No LLM call, no segmentation.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS text.llm_qa_keywords_by_ask (
    ask_id     BIGINT      NOT NULL,
    keyword    TEXT        NOT NULL,
    kind       TEXT        NOT NULL,
    code       TEXT,                       -- denormalized from the ask row
    product    TEXT,                       -- denormalized from the ask row
    ask_day    DATE,                       -- denormalized Asia/Shanghai day of ask_date
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_llm_qa_keywords_by_ask PRIMARY KEY (keyword, ask_id),
    CONSTRAINT fk_llm_qa_keywords_by_ask_ask FOREIGN KEY (ask_id)
        REFERENCES text.llm_qa_by_ask (ask_id) ON DELETE CASCADE,
    CONSTRAINT ck_llm_qa_keywords_by_ask_kind CHECK (
        kind IN ('code', 'name', 'product', 'item', 'series'))
);

-- Code-scoped keyword browsing (the security's ask-history keywords).
CREATE INDEX IF NOT EXISTS ix_llm_qa_keywords_by_ask_code_day
    ON text.llm_qa_keywords_by_ask (code, ask_day DESC);

COMMENT ON COLUMN text.llm_qa_keywords_by_ask.kind IS
  'Where the keyword came from: code = instrument code; name = instrument '
  'display name; product = product id / page path; item = in-plot state '
  'value or searchKeyword (the active toggles/indicators); series = plotted '
  'series name.';

COMMENT ON COLUMN text.llm_qa_keywords_by_ask.ask_day IS
  'ask_date bucketed to an Asia/Shanghai day, denormalized so date-scoped '
  'keyword queries stay single-table (same denormalization pattern as '
  'text.news_keywords.date).';

COMMENT ON TABLE text.llm_qa_keywords_by_ask IS
  'Keyword index for interactive AI asks (text.news_keywords analog): one '
  'row per (keyword, ask_id), derived deterministically from the ask '
  'payload at persist time. The AiPage by-ask search hits this table OR '
  'question/answer ILIKE; code/product/ask_day are denormalized for '
  'single-table scoped lookups.';
