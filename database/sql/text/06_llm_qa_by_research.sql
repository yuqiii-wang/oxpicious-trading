-- ============================================================================
--  LLM Q&A by research — research-logic knowledge base
--  Table: text.llm_qa_by_research — the industry/security LOGIC analog of
--  text.llm_qa (02_llm_qa.sql): same question/answer + provenance shape,
--  but each row explains the UNDERLYING industry/security logic (business
--  profile, scenario forecasts, risk factors, current market-capital
--  interests) instead of a recent price move. Consequently it has NO
--  is_big_fluctuation.
--
--  GENERATION CONTRACT (llm_agents.llm_qa_by_research.generation — six
--  separate LLM requests per row): requests 1-5 each produce one
--  evaluation dimension — intro (general investors' logic: what it is
--  and does), optimistic_forecast (bull-case 6-12m outlook),
--  pessimistic_forecast (bear-case 6-12m outlook), risks (what breaks the
--  logic), capitalization (current market-capital interests: cap
--  structure / where attention sits) — each stored in its own column;
--  the 6th request SUMMARIZES the five dimensions into answer (the
--  row's display content) and assigns logic_label. The five dimension
--  requests are web-searched; all their reference hits are fast-resolved
--  into text.news rows and merged into ONE news group (the row's refs
--  mapping — text.llm_qa_refs FKs text.llm_qa only, so the group IS the
--  per-ref provenance here), with the per-dimension reference blocks
--  kept in context.
--
--  logic_label is a label-code column: SMALLINT NOT NULL, decoded through
--  static.label_dim (domain research_logic) at the API boundary. NOT a PG
--  ENUM (banned — they break bulk COPY casts) and no per-table CHECK: the
--  vocabulary lives in the label_dim mirrors (label_dim INSERT file
--  static/label_dim/07_text_llm.sql, _common/labels.py, label-codes.ts).
--  Extending it = one INSERT per mirror.
-- ============================================================================

CREATE TABLE IF NOT EXISTS text.llm_qa_by_research (
    qa_id          BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question       TEXT        NOT NULL,
    answer         TEXT        NOT NULL,      -- the 6th request's SUMMARY of the five dimensions (the row's display content); capped 2000 (10_llm_summary_caps.sql)
    context        TEXT,                       -- merged per-dimension reference blocks — never rendered; capped 8000 (10_llm_summary_caps.sql)
    intro          TEXT,                       -- dimension 1: general investors' logic — what it is and does
    optimistic_forecast TEXT,                  -- dimension 2: the 6-12 month OPTIMISTIC-scenario outlook (bull path, catalysts)
    pessimistic_forecast TEXT,                 -- dimension 3: the 6-12 month PESSIMISTIC-scenario outlook (bear path, warning signals)
    risks          TEXT,                       -- dimension 4: what breaks the logic
    capitalization TEXT,                       -- dimension 5: current market-capital interests (cap structure / where attention sits)
    logic_label    SMALLINT    NOT NULL,       -- dominant investing logic (static.label_dim domain research_logic)
    category       TEXT,                       -- null for now
    industry_id    TEXT,
    sector_id      TEXT,                       -- denormalized parent sector of industry_id
    news_group_id  INTEGER,                    -- reference set (see news_group_items)
    llm_model      TEXT,
    request_ids    BIGINT[]    NOT NULL DEFAULT '{}',  -- the generation flow's 6 text.llm_requests ids (5 dimensions + summary)
    dim_refs       JSONB,                      -- per-dimension ref resolution: {"intro": [{"ref","news_id","is_used","ref_time"}…], …}
    language       TEXT        DEFAULT 'zh',
    is_active      BOOLEAN     NOT NULL DEFAULT true,
    has_refs       BOOLEAN     NOT NULL DEFAULT false,
    is_failed_explanation BOOLEAN NOT NULL DEFAULT true,
    qa_date        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_tsv     TSVECTOR,                  -- weighted full-text search vector: question A, answer B, five dimensions C, context D

    CONSTRAINT uq_llm_qa_by_research UNIQUE (question, news_group_id),
    CONSTRAINT fk_llm_qa_by_research_news_groups FOREIGN KEY (news_group_id)
        REFERENCES text.news_groups (news_group_id)
);

-- Sector-scope filter for the AI page's L1-only picks.
CREATE INDEX IF NOT EXISTS ix_llm_qa_by_research_sector
    ON text.llm_qa_by_research (sector_id);

-- Industry-scope filter + per-day calendar lookups (industry_id + qa_date).
CREATE INDEX IF NOT EXISTS ix_llm_qa_by_research_industry_qa_date
    ON text.llm_qa_by_research (industry_id, qa_date);

-- Label-scoped feeds: the table's differentiating query (one logic's
-- research rows, newest first).
CREATE INDEX IF NOT EXISTS ix_llm_qa_by_research_logic_qa_date
    ON text.llm_qa_by_research (logic_label, qa_date DESC);

-- Full-corpus calendar mapping and qa_date-ordered feeds.
CREATE INDEX IF NOT EXISTS ix_llm_qa_by_research_qa_date
    ON text.llm_qa_by_research (qa_date);

-- Weighted full-text search — same contract as
-- text.news.search_tsv (the query must be segmented the same way).
CREATE INDEX IF NOT EXISTS ix_llm_qa_by_research_search_tsv_gin
    ON text.llm_qa_by_research USING gin (search_tsv);

COMMENT ON COLUMN text.llm_qa_by_research.dim_refs IS
  'Per-dimension REFERENCE RESOLUTION, persisted at store time by '
  'llm_agents.llm_qa_by_research.generation from the request-time '
  'resolve: {"intro": [{"ref": "ref_1", "news_id": 123, "is_used": true, '
  '"ref_time": "…"} …], "optimistic_forecast": …, "pessimistic_forecast": '
  '…, "risks": …, "capitalization": …} — '
  'one entry per search hit of that dimension''s compose request, in hit '
  '(= citation tag) order. is_used is whether the dimension''s stored '
  'answer cites the tag (extract_cited_refs); ref_time the hit''s actual '
  'publish time (NULL unknown). The UI joins news_id against text.news '
  'to expand each inline [来源：ref_N] bracket of a dimension text into '
  'its article (the text.llm_qa + text.llm_qa_refs expandable-tag '
  'pattern, which this table mirrors as a JSONB column because '
  'text.llm_qa_refs FKs text.llm_qa only). NULL = stored before the '
  'column existed — the UI renders the dimension texts plain.';

COMMENT ON COLUMN text.llm_qa_by_research.request_ids IS
  'The generation flow''s OWN LLM requests as text.llm_requests ids, in '
  'flow order: the five dimension requests (intro / optimistic_forecast / '
  'pessimistic_forecast / risks / capitalization — each a separate '
  'single-purpose ask over its own searched references) then the 6th '
  'summary request. Backlink into the '
  'source log: join text.llm_requests to recover each request''s full '
  'payload, response, and context_ref_ids. Collected by '
  'llm_agents.llm_qa_by_research.generation after the requests settle '
  '(service + flow-window match on the chat-completions endpoint); '
  'empty for rows stored before the log existed or when collection '
  'failed (non-fatal).';

COMMENT ON COLUMN text.llm_qa_by_research.answer IS
  'The 6th request''s SUMMARY of the five dimension answers — the row''s '
  'display content (the UI shows this as the card body; the dimensions are '
  'the expandable detail). Replaces any earlier whole-answer semantics.';

COMMENT ON COLUMN text.llm_qa_by_research.intro IS
  'Dimension 1 (general investors'' logic): what the mapped '
  'industry/security IS and does — business profile, value chain position, '
  'the logic a general investor should first understand. NULL when its '
  'request failed.';

COMMENT ON COLUMN text.llm_qa_by_research.optimistic_forecast IS
  'Dimension 2 (optimistic forecast): the 6-12 month OPTIMISTIC-scenario '
  'outlook — the bull path when favorable conditions (policy support, '
  'demand upside, technology breakthrough, loose liquidity) play out, its '
  'return elasticity, main catalysts, and the conditions / watch signals '
  'that confirm the scenario. NULL when its request failed.';

COMMENT ON COLUMN text.llm_qa_by_research.pessimistic_forecast IS
  'Dimension 3 (pessimistic forecast): the 6-12 month PESSIMISTIC-scenario '
  'outlook — the bear path when adverse conditions (demand rollover, '
  'policy tightening, competitive deterioration, tight liquidity) play '
  'out, the main risk exposure / drawdown sources, and the conditions / '
  'warning signals that confirm the scenario. NULL when its request '
  'failed.';

COMMENT ON COLUMN text.llm_qa_by_research.risks IS
  'Dimension 4 (risks): what BREAKS the logic — policy/regulatory, demand '
  'rollover, competition/commoditization, technology substitution, '
  'leverage, …. NULL when its request failed.';

COMMENT ON COLUMN text.llm_qa_by_research.capitalization IS
  'Dimension 5 (current market-capital interests): the underlying''s '
  'current capitalization structure and where market interest sits — cap '
  'size/mix, index weighting, institutional vs retail attention, recent '
  'fund-flow/positioning commentary. NULL when its request failed.';

COMMENT ON COLUMN text.llm_qa_by_research.logic_label IS
  'Dominant investing logic of the row''s industry/security, stored as a '
  'static.label_dim SMALLINT code (domain research_logic; 0-based — '
  'growth=0 value=1 dividend=2 commodity=3 cyclical=4 defensive=5 '
  'quality=6 theme=7, 99 other = the fallback: the logic did not fit / '
  'the model could not classify). Decoded at the API boundary only; the '
  'vocabulary lives in the label_dim mirrors (07_text_llm.sql, '
  '_common.labels.RESEARCH_LOGIC_CODE, label-codes.ts) — extend by one '
  'INSERT per mirror.';

COMMENT ON COLUMN text.llm_qa_by_research.qa_date IS
  'The question''s own data date (Asia/Shanghai midnight); manual/undated '
  'rows default to now(). Same mapping contract as text.llm_qa.qa_date '
  '(the AI page maps it to the latest trading day on or before it).';

COMMENT ON COLUMN text.llm_qa_by_research.has_refs IS
  'Whether the row''s refs mapping carries sources: true iff the linked '
  'news group (news_group_id -> text.news_group_items) has items — the '
  'generation flow fast-resolves every dimension request''s hits into '
  'text.news rows and merges them into that ONE group (the by-research '
  'refs mapping; text.llm_qa_refs FKs text.llm_qa only, so the group is '
  'the per-ref provenance here). false when no dimension request '
  'surfaced hits or the group write failed (non-fatal).';

COMMENT ON COLUMN text.llm_qa_by_research.is_failed_explanation IS
  'True when the answer is refusal boilerplate rather than a substantive '
  'explanation — same derivation as text.llm_qa.is_failed_explanation '
  '(llm_agents.llm_qa.FAILED_EXPLANATION_KEYWORDS at store time; defaults '
  'true for unassessed rows).';

COMMENT ON COLUMN text.llm_qa_by_research.search_tsv IS
  'Weighted search tsvector (simple config over segmented text): question A, '
  'answer B, the five dimension texts C (intro / optimistic_forecast / '
  'pessimistic_forecast / risks / capitalization), context D. Same contract '
  'as text.news.search_tsv.';

COMMENT ON TABLE text.llm_qa_by_research IS
  'Research-logic Q&A knowledge base: one row per curated question/answer '
  'explaining UNDERLYING industry/security logic (intro / scenario '
  'forecasts / risks sections + logic_label), with the same news-group provenance and '
  'idempotent (question, news_group_id) write API as text.llm_qa — but, '
  'unlike llm_qa, no is_big_fluctuation (no price-move anchoring). Written '
  'by llm_agents.llm_qa_by_research.';
