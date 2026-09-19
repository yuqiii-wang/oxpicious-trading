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
    sector_id      TEXT,                       -- denormalized parent sector of industry_id
    news_group_id  INTEGER,                    -- reference set (see news_group_items)
    llm_model      TEXT,
    language       TEXT        DEFAULT 'zh',
    is_active      BOOLEAN     NOT NULL DEFAULT true,
    has_refs       BOOLEAN     NOT NULL DEFAULT false,
    is_failed_explanation BOOLEAN NOT NULL DEFAULT true,
    qa_date        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_llm_qa UNIQUE (question, news_group_id),
    CONSTRAINT fk_llm_qa_news_groups FOREIGN KEY (news_group_id)
        REFERENCES text.news_groups (news_group_id)
);

-- Upgrade: has_refs — whether per-reference resolution wrote
-- text.llm_qa_refs rows for this answer. Set by
-- llm_agents.online_search_summary.storage.store after the refs upsert
-- to mirror the table: true iff ref rows exist, false when resolution
-- surfaced nothing or the refs write failed (refs failure is
-- deliberately non-fatal — the Q&A itself still stores).

-- Upgrade: is_failed_explanation — the answer is refusal boilerplate
-- ("无法解释" / "无法提供…实质性原因要点" etc.), not a substantive
-- explanation. Derived at store time by llm_agents.llm_qa.upsert_qa
-- from FAILED_EXPLANATION_KEYWORDS: any keyword hit in the answer -> 
-- true, none -> false. Defaults true (unassessed rows count as failed).

-- Sector-scope filter for the AI page's L1-only picks.
CREATE INDEX IF NOT EXISTS ix_llm_qa_sector ON text.llm_qa (sector_id);

-- Industry-scope filter + per-day calendar/items lookups on the AI page
-- and the agents' dedupe checks (industry_id + qa_date).
CREATE INDEX IF NOT EXISTS ix_llm_qa_industry_qa_date
    ON text.llm_qa (industry_id, qa_date);

-- Full-corpus calendar mapping and qa_date-ordered feeds.
CREATE INDEX IF NOT EXISTS ix_llm_qa_qa_date ON text.llm_qa (qa_date);

COMMENT ON COLUMN text.llm_qa.qa_date IS
  'The question''s own data date (Asia/Shanghai midnight): for '
  'llm_agents.industry_qa_weekly rows the （截至…） ranking date the '
  'answer is anchored to; manual/undated rows default to now(). The AI '
  'page maps it to the latest trading day on or before it for the '
  'calendar/event strip and the per-card date label.';

COMMENT ON COLUMN text.llm_qa.sector_id IS
  'Parent sector of industry_id in the canonical taxonomy (FIN for BANKS, '
  'BROAD for BROAD_SSE, …), denormalized by llm_agents so an L1-only scope '
  'filters on this column directly. Set iff industry_id is set; existing '
  'rows are backfilled by re-storing them.';

COMMENT ON COLUMN text.llm_qa.has_refs IS
  'Whether text.llm_qa_refs rows exist for this answer (set by '
  'llm_agents.online_search_summary.storage.store to mirror the refs '
  'table). False when resolution surfaced no refs or the refs write '
  'failed — refs failure is non-fatal, the Q&A itself still stores.';

COMMENT ON COLUMN text.llm_qa.is_failed_explanation IS
  'True when the answer is refusal boilerplate rather than a substantive '
  'explanation (keyword hit on 无法解释 / 无法提供…实质性原因要点 / '
  '没有来源支持 etc. — llm_agents.llm_qa.FAILED_EXPLANATION_KEYWORDS). '
  'Derived at store time; defaults true for unassessed rows.';

COMMENT ON TABLE text.llm_qa IS
  'Curated Q&A knowledge base for LLM retrieval. One row per question/answer '
  'pair, with news-group provenance (text.news_groups -> text.news_group_items), '
  'optional industry/sector linkage, and the model that generated the answer.';
