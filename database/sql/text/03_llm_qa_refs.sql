-- ============================================================================
--  LLM Q&A per-reference resolution
--  Table: text.llm_qa_refs — one row per citation tag (ref_N) of a stored
--  text.llm_qa answer, typing WHERE the referenced article came from and
--  linking it to its text.news row. ref_type is PROVENANCE, not strength:
--
--    ref_type = 'exact'     — the article is the one the provider's summary
--                             response returned: echo metadata stored as-is
--                             (verified or not), or the echo's own link
--                             fetched and content-confirmed. Always
--                             resolved_via = 'summary'.
--    ref_type = 'relevant'  — the article was found AFTER the summary by
--                             title search from other sources: a stored
--                             corpus title match (resolved_via = 'corpus')
--                             or a ddgs-discovered candidate page
--                             (resolved_via = 'ddgs'; the text.news row
--                             carries the fetched full content).
--
--  Unresolved references ("search not found") get a NULL-CONTENT
--  placeholder text.news row (title/source/date only — the same shape as
--  the titles-only gov/ndrc rows) typed 'exact' — the metadata IS the
--  response's own — so every ref links to a news_id and can be
--  re-resolved later.
-- ============================================================================

CREATE TABLE IF NOT EXISTS text.llm_qa_refs (
    qa_id        BIGINT      NOT NULL,
    ref          TEXT        NOT NULL,     -- citation tag (ref_1) as cited in the answer
    ref_type     TEXT        NOT NULL,     -- 'exact' (from summary) | 'relevant' (post-hoc title match)
    news_id      BIGINT      NOT NULL,     -- resolved article; null-content placeholder when unresolved
    resolved_url TEXT,                     -- candidate URL the exact-verification ran against (NULL when none)
    ref_time     TIMESTAMPTZ NOT NULL DEFAULT now(),  -- reference's publish time per the search response; now() when absent
    resolved_via TEXT,                     -- 'summary' | 'corpus' | 'ddgs' | 'zhihu' — the provenance behind ref_type
    CONSTRAINT pk_llm_qa_refs PRIMARY KEY (qa_id, ref),
    CONSTRAINT fk_llm_qa_refs_qa FOREIGN KEY (qa_id)
        REFERENCES text.llm_qa (qa_id) ON DELETE CASCADE,
    CONSTRAINT fk_llm_qa_refs_news FOREIGN KEY (news_id)
        REFERENCES text.news (news_id),
    CONSTRAINT ck_llm_qa_refs_ref_type CHECK (ref_type IN ('exact', 'relevant')),
    CONSTRAINT ck_llm_qa_refs_resolved_via CHECK (resolved_via IN ('summary', 'corpus', 'ddgs', 'zhihu'))
);

-- Upgrade: 'zhihu' joined the resolved_via vocabulary when the zhihu
-- provider path landed in RefResolver (storage.resolve step 1) —
-- deployments created before it carry the narrower three-value check.
ALTER TABLE text.llm_qa_refs DROP CONSTRAINT IF EXISTS ck_llm_qa_refs_resolved_via;
ALTER TABLE text.llm_qa_refs ADD CONSTRAINT ck_llm_qa_refs_resolved_via
    CHECK (resolved_via IN ('summary', 'corpus', 'ddgs', 'zhihu'));

-- Reverse lookup: which answers reference this article.
CREATE INDEX IF NOT EXISTS ix_llm_qa_refs_news_id
    ON text.llm_qa_refs (news_id);

COMMENT ON COLUMN text.llm_qa_refs.ref_type IS
  'exact = the article came with the provider''s summary response (echo '
  'metadata, or its own link content-confirmed); relevant = found after '
  'the summary by title search (corpus match or ddgs candidate).';

COMMENT ON COLUMN text.llm_qa_refs.resolved_via IS
  'Provenance behind ref_type: summary = the response''s own article; '
  'corpus = title match against the stored text.news corpus; ddgs = '
  'ddgs title search + markitdown content confirmation. Set by '
  'llm_agents.online_search_summary.storage.resolve/store.';

COMMENT ON COLUMN text.llm_qa_refs.news_id IS
  'Resolved text.news article. Unresolved refs point to a null-content '
  'placeholder row (title/source/date only) so the link is always total.';

COMMENT ON COLUMN text.llm_qa_refs.ref_time IS
  'The reference''s publish time as reported by the search response '
  '(publish_date, midnight Asia/Shanghai); now() at insert when the '
  'response carried no date.';

COMMENT ON TABLE text.llm_qa_refs IS
  'Per-reference resolution of one stored Q&A: citation tag -> ref_type '
  '(exact|relevant) -> text.news article. Complements the coarse '
  'text.news_groups provenance with per-ref typing.';
