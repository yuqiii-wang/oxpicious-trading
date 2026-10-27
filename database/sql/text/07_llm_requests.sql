-- ============================================================================
--  LLM request/response source log
--  Table: text.llm_requests — one row per SOURCE LLM API request the
--  llm_agents make: the full request payload (messages/context as sent)
--  and the FINAL attempt's response payload, alongside the coarse outcome
--  the lean monitor.external_resource_audit already carries (that table
--  answers "which process hit which endpoint at what cost"; THIS table
--  answers "what exactly did we send and what came back").
--
--  Written fire-and-forget by llm_agents/_core/request_log.py, hooked into
--  llm_agents/_core/transport.py RetryingJsonClient.post_json — the JSON
--  POST transport shared by EVERY llm_agents provider — so one hook covers
--  llm_ask / online_search_summary / news_digestions / llm_qa_by_research.
--  Retries are not itemized (same grain as the audit): one row per
--  logical call, the final attempt's response.
--
--  CONTEXTS AND REFERENCES BY REF IDS: the prompt-embedded context and
--  reference articles are ALSO linkable back to their source rows instead
--  of being recoverable only from the payload blob. Callers bind the
--  source ids around the request (llm_agents._core.request_log.bind_ref_ids
--  — a contextvar the transport reads at record time); readers then LOAD
--  the source data by these ref ids (request_log.load_request_sources /
--  a join against text.news), not by re-parsing payloads:
--    context_ref_ids — the text.news article ids whose stored content fed
--                      the request (digest source article, search
--                      references, corpus context).
--    news_group_id   — the text.news_groups set the request's sources
--                      belong to, when one exists (the llm_qa flow).
--    qa_id           — the text.llm_qa row the request produced, when the
--                      answer is later stored (backfilled by the storing
--                      agents; NULL for ask-style one-offs).
--
--  service stays TEXT on purpose (open-ended vocabulary — the same
--  exception as monitor.external_resource_audit.service). provider/model
--  stay TEXT: the text.* LLM-layer vocabularies are deliberately NOT
--  label-code converted (01_label_dim.sql header).
--
--  Payload hygiene (enforced at write time by request_log): data-URL
--  screenshots are replaced by a short placeholder (a base64 screenshot
--  must not bloat the table), payloads are JSONB so queries can reach
--  into messages without a re-parse.
--
--  Applied after 06_llm_qa_by_research.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS text.llm_requests (
    request_id       BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    service          TEXT        NOT NULL DEFAULT 'unknown',
    provider         TEXT        NOT NULL,
    model            TEXT,
    endpoint         TEXT        NOT NULL,  -- scheme://host/path, query string stripped
    request_payload  JSONB,                 -- the request body as sent (screenshots placeholder-ed)
    response_payload JSONB,                 -- the FINAL attempt's parsed response body; NULL on failure
    status_code      SMALLINT,              -- HTTP status of the final attempt; NULL = no response at all
    is_failed        BOOLEAN     NOT NULL DEFAULT false,
    failure_reason   TEXT,                  -- NULL on success; short reason on failure
    duration_ms      INTEGER,               -- wall time of the WHOLE logical call (retries + backoff)
    context_ref_ids  BIGINT[]    NOT NULL DEFAULT '{}',  -- text.news ids of the request's context/reference articles
    news_group_id    INTEGER,               -- text.news_groups set of the sources, when one exists
    qa_id            BIGINT,                -- text.llm_qa row the request produced, when stored
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- GMT (UTC) day of created_at (boundary Beijing 08:00) — the same
    -- operational-day semantics as monitor.external_resource_audit.date.
    -- GENERATED — writers never set it.
    date             date        GENERATED ALWAYS AS ((created_at AT TIME ZONE 'GMT')::date) STORED,

    CONSTRAINT fk_llm_requests_news_groups FOREIGN KEY (news_group_id)
        REFERENCES text.news_groups (news_group_id),
    CONSTRAINT fk_llm_requests_qa FOREIGN KEY (qa_id)
        REFERENCES text.llm_qa (qa_id)
);

-- Recent-first feeds ("what did we ask in the last hour").
CREATE INDEX IF NOT EXISTS ix_llm_requests_created_at
    ON text.llm_requests (created_at DESC);

-- Per-day operational grouping (matches the audit table's date index).
CREATE INDEX IF NOT EXISTS ix_llm_requests_date
    ON text.llm_requests (date);

-- "Which requests saw this article" — the ref-id loading path.
CREATE INDEX IF NOT EXISTS ix_llm_requests_context_refs
    ON text.llm_requests USING GIN (context_ref_ids);

-- Per-answer rollup after the storing agents backfill qa_id.
CREATE INDEX IF NOT EXISTS ix_llm_requests_qa_id
    ON text.llm_requests (qa_id) WHERE qa_id IS NOT NULL;

COMMENT ON TABLE text.llm_requests IS
    'Source LLM request/response log: one row per logical llm_agents API '
    'call (final attempt only, retries not itemized) with the full '
    'request payload and the final response payload as JSONB. Written '
    'fire-and-forget by llm_agents/_core/request_log.py from '
    'llm_agents/_core/transport.py RetryingJsonClient — the transport '
    'every llm_agents provider shares. Context/reference source articles '
    'link back by ref ids (context_ref_ids -> text.news, news_group_id -> '
    'text.news_groups, qa_id -> text.llm_qa) so source data loads by id '
    'instead of payload re-parsing; screenshotted data URLs are '
    'placeholder-ed at write time.';

COMMENT ON COLUMN text.llm_requests.service IS
    'The writing process''s service name (the same dotted-module vocabulary '
    'as monitor.external_resource_audit.service — resolved the same way). '
    'TEXT on purpose: open-ended vocabulary.';

COMMENT ON COLUMN text.llm_requests.provider IS
    'The llm_agents provider name (transport client name, e.g. zhipu / '
    'zhihu). TEXT: the text.* LLM-layer vocabularies are deliberately not '
    'label-code converted.';

COMMENT ON COLUMN text.llm_requests.request_payload IS
    'The request body exactly as sent (JSONB). Screenshots arrive as '
    'short placeholder strings, not base64 — the image bytes are not '
    'source data worth storing.';

COMMENT ON COLUMN text.llm_requests.response_payload IS
    'The FINAL attempt''s parsed JSON response body (JSONB). NULL when '
    'the call failed without a usable body — failure_reason carries the '
    'why.';

COMMENT ON COLUMN text.llm_requests.context_ref_ids IS
    'text.news article ids whose stored content fed this request '
    '(digestion source article, search references, corpus context) — '
    'bound by the calling agent via request_log.bind_ref_ids around the '
    'request. Load the source data by joining these ids against '
    'text.news; do not re-parse request_payload to recover it.';

COMMENT ON COLUMN text.llm_requests.news_group_id IS
    'The text.news_groups set the request''s sources belong to, when one '
    'exists (the llm_qa flows group their source articles).';

COMMENT ON COLUMN text.llm_requests.qa_id IS
    'The text.llm_qa row this request produced, when the answer was '
    'stored (backfilled by the storing agents); NULL for ask-style '
    'one-offs whose answers never land in the Q&A knowledge base.';

COMMENT ON COLUMN text.llm_requests.date IS
    'GENERATED: created_at bucketed to a GMT (UTC) day — a new date '
    'starts at Beijing 08:00, the same operational-day boundary as '
    'monitor.external_resource_audit.date.';
