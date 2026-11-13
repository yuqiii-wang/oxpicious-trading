-- ============================================================================
--  LLM Q&A per-reference resolution
--  Table: llm.llm_qa_refs — one row per RESOLVED ARTICLE of a stored
--  llm.llm_qa answer (PK (qa_id, news_id)), typing WHERE the article came
--  from and whether the answer actually cited it. ref_type is PROVENANCE,
--  not strength:
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
--
--  `ref` is the REPRESENTATIVE citation tag of the article: the smallest
--  tag the answer cites among the refs resolving to it, else the smallest
--  tag — two tags hitting the same page collapse onto one row. `is_used`
--  is the search/LLM separation check: true iff the answer cites any of
--  the article's tags (set by the writers from extract_cited_refs(answer);
--  refs the summarizer never cited load too, unused).
-- ============================================================================

CREATE TABLE IF NOT EXISTS llm.llm_qa_refs (
    qa_id        BIGINT      NOT NULL,
    ref          TEXT        NOT NULL,     -- representative citation tag (ref_1)
    ref_type     TEXT        NOT NULL,     -- 'exact' (from summary) | 'relevant' (post-hoc title match)
    news_id      BIGINT      NOT NULL,     -- resolved article; null-content placeholder when unresolved
    is_used      BOOLEAN     NOT NULL DEFAULT false,  -- the answer cites the article (any of its tags)
    resolved_url TEXT,                     -- candidate URL the exact-verification ran against (NULL when none)
    ref_time     TIMESTAMPTZ,              -- reference's ACTUAL publish time (response date or URL date); NULL = unknown — never now()/qa-date
    resolved_via TEXT,                     -- 'summary' | 'corpus' | 'ddgs' | 'zhihu' — the provenance behind ref_type
    CONSTRAINT pk_llm_qa_refs PRIMARY KEY (qa_id, news_id),
    CONSTRAINT uq_llm_qa_refs_qa_ref UNIQUE (qa_id, ref),
    CONSTRAINT fk_llm_qa_refs_qa FOREIGN KEY (qa_id)
        REFERENCES llm.llm_qa (qa_id) ON DELETE CASCADE,
    CONSTRAINT fk_llm_qa_refs_news FOREIGN KEY (news_id)
        REFERENCES text.news (news_id),
    CONSTRAINT ck_llm_qa_refs_ref_type CHECK (ref_type IN ('exact', 'relevant')),
    CONSTRAINT ck_llm_qa_refs_resolved_via CHECK (resolved_via IN ('summary', 'corpus', 'ddgs', 'zhihu'))
);

-- Tag lookup keeps working without the PK: the UI expands refs by tag and
-- orders by ref.
CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_qa_refs_qa_ref
    ON llm.llm_qa_refs (qa_id, ref);

-- Reverse lookup: which answers reference this article.
CREATE INDEX IF NOT EXISTS ix_llm_qa_refs_news_id
    ON llm.llm_qa_refs (news_id);

COMMENT ON COLUMN llm.llm_qa_refs.ref_type IS
  'exact = the article came with the provider''s summary response (echo '
  'metadata, or its own link content-confirmed); relevant = found after '
  'the summary by title search (corpus match or ddgs candidate).';

COMMENT ON COLUMN llm.llm_qa_refs.resolved_via IS
  'Provenance behind ref_type: summary = the response''s own article; '
  'corpus = title match against the stored text.news corpus; ddgs = '
  'ddgs title search + markitdown content confirmation. Set by '
  'llm_agents.online_search_summary.storage.resolve/store.';

COMMENT ON COLUMN llm.llm_qa_refs.news_id IS
  'Resolved text.news article. Unresolved refs point to a null-content '
  'placeholder row (title/source/date only) so the link is always total.';

COMMENT ON COLUMN llm.llm_qa_refs.ref_time IS
  'The reference''s ACTUAL publish time: the search response''s publish_date '
  '(midnight Asia/Shanghai), else the date embedded in the article URL. '
  'NULL = unknown — a now()/qa-date fallback here used to display the '
  'row-creation date as the article''s date. Writers also drop refs whose '
  'known date falls outside [qa interest day - 20d, qa interest day].';

COMMENT ON COLUMN llm.llm_qa_refs.is_used IS
  'true iff the stored answer cites this article (any of its citation '
  'tags, extract_cited_refs of llm.llm_qa.answer). Search results the '
  'summarizer did not cite are kept as false — refs load regardless. '
  'Set by the llm_agents/builds writers; retro-fitted by '
  'temp_scripts/qa_refs_is_used_pk_migration.py.';

COMMENT ON COLUMN llm.llm_qa_refs.ref IS
  'Representative citation tag (ref_N) of the article: the smallest tag '
  'the answer cites among the refs resolving to it, else the smallest '
  'tag overall. (qa_id, ref) stays UNIQUE for tag lookups.';

COMMENT ON TABLE llm.llm_qa_refs IS
  'Per-reference resolution of one stored Q&A: resolved article (PK with '
  'qa_id) -> ref_type (exact|relevant), representative citation tag, '
  'is_used. Complements the coarse text.news_groups provenance with '
  'per-ref typing and usage.';
