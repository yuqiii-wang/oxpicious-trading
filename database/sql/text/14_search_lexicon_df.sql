-- ============================================================================
--  text.search_lexicon_df — overly-common lexeme stats per searchable table
--
--  One row per (search_table, lexeme) whose document frequency is >= 25%
--  of the table's rows (LEXICON_DF_MIN_RATIO in
--  _common/tokenization/search_indexing.py — the floor lives there, the
--  Python refresh is the only writer). Populated from
--  ts_stat('SELECT search_tsv FROM <table>') by
--  SearchIndexer.refresh_lexicon_df_async after a table's search_tsv write
--  pass completes (builds.text for news / news_comments / llm_qa, the
--  llm_agents storages for the rest).
--
--  WHY: the MCP text_search tool ranks with ORDER BY ts_rank(...) DESC —
--  a sort that must detoast and score EVERY matched row. When EVERY query
--  lexeme is overly common (市场 alone, or a query whose words are all in
--  25%+ of articles) the matched set is most of the table and the rank
--  sort dominates the query (~0.2s at 28k articles, growing linearly with
--  the corpus). text_search checks this table: all-common queries order by
--  the article date instead (rank stays a returned column + tiebreak).
--  ANDing a common lexeme with a rarer one is LEFT ALONE — the GIN bitmap
--  intersection only shrinks the matched set, so common lexemes are never
--  dropped from the tsquery (2026-11-01 study, temp_scripts/
--  probe_gin_search_explain.py).
--
--  Staleness is benign: an unflagged common lexeme keeps the rank-ordered
--  (slower) path; nothing here affects match semantics — only the ORDER BY
--  strategy. search_table decodes via static.label_dim domain
--  'search_table' (SMALLINT label-code contract; mirrors:
--  _common/labels.py SEARCH_TABLE_CODE, data_viz/api/services/
--  label-codes.ts).
-- ============================================================================

CREATE TABLE IF NOT EXISTS text.search_lexicon_df (
    search_table  SMALLINT    NOT NULL,     -- 'search_table': the TSV_SPECS table this row's DF counts
    lexeme        TEXT        NOT NULL,     -- one search_tsv lexeme ('simple' config word)
    doc_freq      INTEGER     NOT NULL,     -- # of the table's rows whose search_tsv carries the lexeme
    total_docs    INTEGER     NOT NULL,     -- the table's row count at refresh time (ratio = doc_freq/total_docs)
    refreshed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_search_lexicon_df PRIMARY KEY (search_table, lexeme)
);

COMMENT ON TABLE text.search_lexicon_df IS
  'Overly-common lexeme stats per searchable text table (search_tsv '
  'columns): the >= 25%-DF lexemes maintained by SearchIndexer.'
  'refresh_lexicon_df_async. Read by MCP text_search to switch '
  'all-common-word queries to date ordering; affects ORDER BY strategy '
  'only, never match semantics.';

COMMENT ON COLUMN text.search_lexicon_df.search_table IS
  'Which searchable table the DF counts (decodes via static.label_dim '
  'domain search_table: 0 news, 1 news_comments, 2 news_digestions, '
  '3 llm_qa, 4 llm_qa_by_ask, 5 llm_qa_by_research — the TSV_SPECS order).';

COMMENT ON COLUMN text.search_lexicon_df.lexeme IS
  'One search_tsv lexeme — a StructBERT-segmented word under the simple '
  'config; the query side looks up its segmented tokens here.';

COMMENT ON COLUMN text.search_lexicon_df.doc_freq IS
  'Document frequency: number of the table''s rows whose search_tsv '
  'carries the lexeme at the last refresh.';

COMMENT ON COLUMN text.search_lexicon_df.total_docs IS
  'The table''s row count at refresh time; the commonness ratio is '
  'doc_freq / total_docs >= 0.25.';
