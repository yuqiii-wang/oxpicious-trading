
-- ----------------------------------------------------------------------------
--  Ask context — the chart the question was asked against (1:1 per ask).
--  Hot filter columns are extracted; the verbatim plotInfo payload is kept
--  as JSONB because series/state shapes vary per product page.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS llm.llm_qa_ask_context (
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
        REFERENCES llm.llm_qa_by_ask (ask_id) ON DELETE CASCADE
);

-- Industry-scope filter for the AI page's L2 picks (via the context join).
CREATE INDEX IF NOT EXISTS ix_llm_qa_ask_context_industry
    ON llm.llm_qa_ask_context (industry_id);

-- Sector-scope filter for the AI page's L1-only picks.
CREATE INDEX IF NOT EXISTS ix_llm_qa_ask_context_sector
    ON llm.llm_qa_ask_context (sector_id);

COMMENT ON COLUMN llm.llm_qa_ask_context.plot_info IS
  'The verbatim plotInfo object POSTed by the AI Ask modal (chart identity, '
  'scope.instruments, window, series stats, in-plot state, searchKeywords) '
  '— series/state shapes vary per product page, so only the hot filter '
  'columns are extracted into real columns.';

COMMENT ON COLUMN llm.llm_qa_ask_context.n_images IS
  'Number of screenshot rows linked through llm_qa_ask_context_images '
  '(the payload screenshot count after the size/count caps).';

COMMENT ON TABLE llm.llm_qa_ask_context IS
  'Chart context of one interactive AI ask (1:1 with llm.llm_qa_by_ask): '
  'extracted filter columns + the verbatim plotInfo JSONB. One context can '
  'reference N images through llm.llm_qa_ask_context_images.';
