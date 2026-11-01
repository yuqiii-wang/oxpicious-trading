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
    input_tokens     INTEGER,               -- usage.prompt_tokens of the final response; NULL when the response carries no usage block
    output_tokens    INTEGER,               -- usage.completion_tokens; NULL likewise
    total_tokens     INTEGER,               -- usage.total_tokens — the whole context the call consumed (input + output)
    context_ref_ids  BIGINT[]    NOT NULL DEFAULT '{}',  -- text.news ids of the request's context/reference articles
    news_group_id    INTEGER,               -- text.news_groups set of the sources, when one exists
    qa_id            BIGINT,                -- text.llm_qa row the request produced, when stored
    chain_id         TEXT,                  -- multi-request flow chain (request_log.bind_request_chain); NULL for one-offs
    prev_request_id  BIGINT REFERENCES text.llm_requests (request_id),  -- the chain's previous request (backfilled by the writer after insert)
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

-- Chain traversal (walk a flow's requests via prev_request_id) and
-- per-chain grouping.
CREATE INDEX IF NOT EXISTS ix_llm_requests_chain_id
    ON text.llm_requests (chain_id) WHERE chain_id IS NOT NULL;

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

COMMENT ON COLUMN text.llm_requests.chain_id IS
  'Chain id of a multi-request FLOW (uuid hex, set by '
  'request_log.bind_request_chain around the flow''s generate call): all '
  'of the flow''s sequential requests share it — e.g. the six-request '
  'llm_qa_by_research flow (five dimension requests + the summary '
  'request). NULL for one-off requests. Within a chain the requests '
  'share prompts/context, so each chained row''s request_payload is '
  'stored INCREMENTALLY (shared messages removed — see '
  'prev_request_id); walk the chain and merge to reconstruct what was '
  'sent (request_log.reconstruct_request_payload).';

COMMENT ON COLUMN text.llm_requests.prev_request_id IS
  'The chain predecessor''s request_id — backfilled by the writer thread '
  'right after each insert batch (previous request of the same chain_id '
  'by request_id order); NULL for the chain''s first request or one-offs '
  '(chain_id IS NULL). The first row of a chain carries the FULL '
  'request_payload; every later chained row stores only the incremental '
  'payload (shared top-level keys and identical messages removed), so '
  'the full request as sent = merge of the chain up to that row.';

COMMENT ON COLUMN text.llm_requests.date IS
    'GENERATED: created_at bucketed to a GMT (UTC) day — a new date '
    'starts at Beijing 08:00, the same operational-day boundary as '
    'monitor.external_resource_audit.date.';

-- ============================================================================
--  Migration: usage token counts (added after the table's first release).
--  Extracted from the final response's usage block at write time so
--  per-call / per-day cost aggregates are plain SQL, no JSONB parsing.
--  Runs BEFORE the token columns' COMMENT statements below — a fresh
--  apply of this file must not comment on columns that do not exist yet.
-- ============================================================================
ALTER TABLE text.llm_requests
    ADD COLUMN IF NOT EXISTS input_tokens  INTEGER,
    ADD COLUMN IF NOT EXISTS output_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS total_tokens  INTEGER;

COMMENT ON COLUMN text.llm_requests.input_tokens IS
    'usage.prompt_tokens of the final attempt''s response — the request''s '
    'input token count, extracted at write time from the response''s '
    'OpenAI-style usage block (request_log._extract_usage). NULL when the '
    'response carries no usage block (web-search POSTs, failures).';

COMMENT ON COLUMN text.llm_requests.output_tokens IS
    'usage.completion_tokens of the final attempt''s response — the '
    'reply''s output token count, extracted the same way as input_tokens.';

COMMENT ON COLUMN text.llm_requests.total_tokens IS
    'usage.total_tokens — the whole context token length the call consumed '
    '(input + output), the per-call cost basis. Extracted at write time '
    'like input/output_tokens so aggregate cost queries need no JSONB '
    'parsing of response_payload.';

-- ============================================================================
--  Migration: full-text search over the requests' INCREMENTAL input and
--  output (added after the table's first release). One new column,
--  search_tsv, + its GIN index — the same contract the text schema's
--  large-text tables carry (news / Q&A; see 01_news.sql's search_tsv and
--  _common/tokenization/search_indexing.py): Postgres' 'simple' config
--  does not segment Chinese, so the vector is NOT generated — the
--  request-log writer (llm_agents/_core/request_log.py) pre-segments the
--  texts with the local StructBERT segmenter and builds the weighted
--  vector in the INSERT:
--
--    strip(setweight(to_tsvector('simple', <incremental input tokens>),'A')
--       || setweight(to_tsvector('simple', <output tokens>), 'B'))
--
--  SOURCES (the request's conversation delta — what searching a run's
--  traffic must recall):
--    A  the request's INCREMENTAL input: request_payload.messages as
--       stored — for a chained row the messages the chain had NOT sent
--       before (the ask, tool results, directives); for a chain's first
--       row / a one-off the full message list. Content parts only
--       (image_url placeholders are already short strings); per-message
--       and total extraction caps apply (request_log extracts bounded
--       text — the vector indexes the IMPORTANT data, not the blob).
--    B  the final response's output: choices[0].message.content plus its
--       tool_calls' function names and (capped) arguments — the model's
--       answer and the calls it made. NO reasoning traces: thinking /
--       reasoning_content is internal and never indexed.
--
--  Query side (segmented the same way): search_tsv @@
--  websearch_to_tsquery('simple', tokenize_for_search(<query>)).
--  PRIMARY CONSUMER: the deep agents' thread-internal search tool
--  (llm_agents/deep_agents/planning/thread_search.py) — scoped to ONE
--  langgraph thread by chain membership (chain_id IN the thread's
--  llm_context_id set), so any-level agent searches only ITS OWN run's
--  recorded traffic. Not a text.search_lexicon_df table: thread scopes
--  are tens of rows — ts_rank needs no DF-aware reordering there.
--
--  Runs BEFORE the column COMMENT below — a fresh apply must not
--  comment on a column that does not exist yet.
-- ============================================================================
ALTER TABLE text.llm_requests
    ADD COLUMN IF NOT EXISTS search_tsv TSVECTOR;

CREATE INDEX IF NOT EXISTS ix_llm_requests_search_tsv_gin
    ON text.llm_requests USING gin (search_tsv);

COMMENT ON COLUMN text.llm_requests.search_tsv IS
    'Weighted full-text vector over the request''s conversation delta: '
    'A = the incremental input (request_payload.messages as stored — a '
    'chained row''s new messages, a chain-first/one-off row''s full '
    'list), B = the output (the response''s assistant text + tool-call '
    'names/arguments). Chinese pre-segmented by the StructBERT '
    'segmenter (never a GENERATED column — ''simple'' does not segment '
    'CJK); positions stripped (GIN indexes lexemes only). NULL = not '
    'yet tokenized (healed by request_log.backfill_search_tsv_async / '
    'the thread-search tool''s scoped heal). Query: search_tsv @@ '
    'websearch_to_tsquery(''simple'', tokenize_for_search(<q>)); the '
    'deep agents'' thread-internal search scopes it by chain_id.';
