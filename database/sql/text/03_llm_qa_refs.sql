-- ============================================================================
--  LLM Q&A per-reference resolution
--  Table: text.llm_qa_refs — one row per RESOLVED ARTICLE of a stored
--  text.llm_qa answer (PK (qa_id, news_id)), typing WHERE the article came
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

CREATE TABLE IF NOT EXISTS text.llm_qa_refs (
    qa_id        BIGINT      NOT NULL,
    ref          TEXT        NOT NULL,     -- representative citation tag (ref_1)
    ref_type     TEXT        NOT NULL,     -- 'exact' (from summary) | 'relevant' (post-hoc title match)
    news_id      BIGINT      NOT NULL,     -- resolved article; null-content placeholder when unresolved
    is_used      BOOLEAN     NOT NULL DEFAULT false,  -- the answer cites the article (any of its tags)
    resolved_url TEXT,                     -- candidate URL the exact-verification ran against (NULL when none)
    ref_time     TIMESTAMPTZ NOT NULL DEFAULT now(),  -- reference's publish time per the search response; now() when absent
    resolved_via TEXT,                     -- 'summary' | 'corpus' | 'ddgs' | 'zhihu' — the provenance behind ref_type
    CONSTRAINT pk_llm_qa_refs PRIMARY KEY (qa_id, news_id),
    CONSTRAINT uq_llm_qa_refs_qa_ref UNIQUE (qa_id, ref),
    CONSTRAINT fk_llm_qa_refs_qa FOREIGN KEY (qa_id)
        REFERENCES text.llm_qa (qa_id) ON DELETE CASCADE,
    CONSTRAINT fk_llm_qa_refs_news FOREIGN KEY (news_id)
        REFERENCES text.news (news_id),
    CONSTRAINT ck_llm_qa_refs_ref_type CHECK (ref_type IN ('exact', 'relevant')),
    CONSTRAINT ck_llm_qa_refs_resolved_via CHECK (resolved_via IN ('summary', 'corpus', 'ddgs', 'zhihu'))
);

-- Tag lookup keeps working without the PK: the UI expands refs by tag and
-- orders by ref.
CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_qa_refs_qa_ref
    ON text.llm_qa_refs (qa_id, ref);

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

COMMENT ON COLUMN text.llm_qa_refs.is_used IS
  'true iff the stored answer cites this article (any of its citation '
  'tags, extract_cited_refs of text.llm_qa.answer). Search results the '
  'summarizer did not cite are kept as false — refs load regardless. '
  'Set by the llm_agents/builds writers; retro-fitted by '
  'temp_scripts/qa_refs_is_used_pk_migration.py.';

COMMENT ON COLUMN text.llm_qa_refs.ref IS
  'Representative citation tag (ref_N) of the article: the smallest tag '
  'the answer cites among the refs resolving to it, else the smallest '
  'tag overall. (qa_id, ref) stays UNIQUE for tag lookups.';

COMMENT ON TABLE text.llm_qa_refs IS
  'Per-reference resolution of one stored Q&A: resolved article (PK with '
  'qa_id) -> ref_type (exact|relevant), representative citation tag, '
  'is_used. Complements the coarse text.news_groups provenance with '
  'per-ref typing and usage.';

-- ============================================================================
--  Migrate: is_used + PK (qa_id, news_id) added 2026-09-19 (the CREATE
--  TABLE above includes them for fresh installs). Existing tables are
--  retro-fitted here: the column defaults false; is_used values and the
--  (qa_id, news_id) dedupe are applied by
--  temp_scripts/qa_refs_is_used_pk_migration.py (bracket-aware citation
--  parser over text.llm_qa.answer — dedupe keeps cited-first rows), then
--  the PK swap below lands once the dupes are gone.
-- ============================================================================
ALTER TABLE text.llm_qa_refs
    ADD COLUMN IF NOT EXISTS is_used BOOLEAN NOT NULL DEFAULT false;

DO $$
DECLARE pk_cols TEXT;
BEGIN
    SELECT string_agg(a.attname, ',' ORDER BY x.ord)
      INTO pk_cols
      FROM pg_constraint c
      CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS x(attnum, ord)
      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = x.attnum
     WHERE c.conrelid = 'text.llm_qa_refs'::regclass
       AND c.contype = 'p';
    IF pk_cols IS DISTINCT FROM 'qa_id,news_id' THEN
        ALTER TABLE text.llm_qa_refs DROP CONSTRAINT pk_llm_qa_refs;
        ALTER TABLE text.llm_qa_refs
            ADD CONSTRAINT pk_llm_qa_refs PRIMARY KEY (qa_id, news_id);
    END IF;
END $$;
