-- ============================================================================
--  News + extracted keywords
--  Tables: text.news, text.news_groups, text.news_group_items,
--          text.news_keywords, text.bm25_corpus_stats, text.news_embeddings
--  Functions: text.refresh_news_keyword_bm25(), text.search_news_bm25()
--    text.news             — one raw article per (title, source, date).
--    text.news_groups      — reference-set entity: a group of news articles
--                            cited together (e.g. the sources behind one AI
--                            QA answer). Membership lives in the junction.
--    text.news_group_items — junction: which articles belong to which group.
--    text.news_keywords    — per-article keyword frequency (FK to news) plus
--                            the BM25 term weight (bm25 column).
--    text.bm25_corpus_stats— singleton row: BM25 corpus stats (N, avgdl, k1, b).
--    text.news_embeddings  — per-article content embedding (NOT IN USE yet).
--
--  Embedding dims = 1536 (OpenAI text-embedding-3-small) for now; revisit
--  (1024 = BAAI/bge-m3) when the embedding pipeline is built.
-- ============================================================================

CREATE TABLE IF NOT EXISTS text.news (
    title       TEXT    NOT NULL,
    content     TEXT,
    date        DATE    NOT NULL,
    source      TEXT,                       -- gov, zhihu, ndrc, ...
    url         TEXT,
    industry_id TEXT,
    word_count  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news PRIMARY KEY (title, source, date)
);

COMMENT ON TABLE text.news IS
  'Raw news articles. One row per (title, source, date). content holds the '
  'article body; industry_id links to the industry taxonomy when applicable.';

-- Reference set: the group of news articles an AI QA answer was based on.
-- Plain entity table — deliberately NOT INHERITS (text.news): table
-- inheritance would leak news_groups rows into SELECT * FROM text.news.
CREATE TABLE IF NOT EXISTS text.news_groups (
    news_group_id SERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE text.news_groups IS
  'Reference-set entity: a group of news articles cited together (e.g. by '
  'one AI QA answer). Membership is in text.news_group_items.';

-- Junction: group membership. One group may cite many articles; one article
-- may be cited by many groups.
CREATE TABLE IF NOT EXISTS text.news_group_items (
    news_group_id INTEGER     NOT NULL,
    news_title    TEXT        NOT NULL,
    source        TEXT        NOT NULL,
    date          DATE        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_group_items
        PRIMARY KEY (news_group_id, news_title, source, date),
    CONSTRAINT fk_news_group_items_group FOREIGN KEY (news_group_id)
        REFERENCES text.news_groups (news_group_id),
    CONSTRAINT fk_news_group_items_news FOREIGN KEY (news_title, source, date)
        REFERENCES text.news (title, source, date)
);

COMMENT ON TABLE text.news_group_items IS
  'Junction between text.news_groups and text.news: the articles each group '
  'cites. PK keeps membership idempotent on re-insert.';

CREATE TABLE IF NOT EXISTS text.news_keywords (
    news_title  TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    date        DATE    NOT NULL,           -- denormalized for join convenience
    keyword     TEXT    NOT NULL,
    count       INTEGER,                     -- raw count in the article
    count_pct   NUMERIC(6,4),                -- count / article_word_count
    bm25        NUMERIC(10,4),               -- BM25 term weight (see below)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_keywords
        PRIMARY KEY (news_title, source, date, keyword),
    CONSTRAINT fk_news_keywords_news FOREIGN KEY (news_title, source, date)
        REFERENCES text.news (title, source, date)
);

-- Existing deployments created before the bm25 column (the CREATE above
-- already includes it on fresh installs; this is a no-op there).
ALTER TABLE text.news_keywords ADD COLUMN IF NOT EXISTS bm25 NUMERIC(10,4);

-- Keyword-leading index: the PK leads with the article, so df computation in
-- refresh_news_keyword_bm25() and keyword lookup in search_news_bm25() need
-- this to avoid a seq scan.
CREATE INDEX IF NOT EXISTS ix_news_keywords_keyword
    ON text.news_keywords (keyword);

COMMENT ON TABLE text.news_keywords IS
  'Per-article keyword frequency extracted from text.news. count = occurrences; '
  'count_pct = count / word_count; bm25 = BM25 term weight (Lucene IDF variant, '
  'k1/b in text.bm25_corpus_stats) — materialized by '
  'text.refresh_news_keyword_bm25() and ranked on by text.search_news_bm25(). '
  'Used for keyword-level sentiment/topic search.';

-- ============================================================================
--  BM25 keyword weighting
--  bm25 = IDF * tf*(k1+1) / (tf + k1*(1 - b + b*dl/avgdl)) with the
--  Lucene IDF ln((N - df + 0.5)/(df + 0.5) + 1), always > 0, so keywords
--  present in most articles decay towards 0 instead of going negative.
--  Weights are corpus-dependent (df/N/avgdl drift as articles accumulate),
--  so they are materialized here and refreshed by
--  text.refresh_news_keyword_bm25() after ingestion runs, not maintained
--  incrementally.
-- ============================================================================
CREATE TABLE IF NOT EXISTS text.bm25_corpus_stats (
    id         SMALLINT     NOT NULL DEFAULT 1,
    n_docs     INTEGER,                    -- articles with >= 1 keyword row
    avgdl      NUMERIC(12,4),              -- avg word_count over the collection
    k1         NUMERIC(6,3) NOT NULL DEFAULT 1.5,   -- TF saturation
    b          NUMERIC(6,3) NOT NULL DEFAULT 0.75,  -- length normalization
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT pk_bm25_corpus_stats PRIMARY KEY (id),
    CONSTRAINT ck_bm25_corpus_stats_singleton CHECK (id = 1)
);

COMMENT ON TABLE text.bm25_corpus_stats IS
  'Singleton (id = 1) BM25 corpus statistics: collection size, average document '
  'length, and the k1/b parameters used to materialize news_keywords.bm25. '
  'Adjust k1/b here, then re-run text.refresh_news_keyword_bm25().';

CREATE OR REPLACE FUNCTION text.refresh_news_keyword_bm25()
RETURNS TABLE (n_docs INTEGER, avgdl NUMERIC, n_keywords INTEGER, n_weighted INTEGER)
LANGUAGE plpgsql
VOLATILE
AS $$
DECLARE
    v_n        INTEGER;
    v_avgdl    NUMERIC;
    v_k1       NUMERIC;
    v_b        NUMERIC;
    v_weighted INTEGER;
BEGIN
    SELECT k1, b INTO v_k1, v_b FROM text.bm25_corpus_stats WHERE id = 1;
    v_k1 := COALESCE(v_k1, 1.5);
    v_b  := COALESCE(v_b, 0.75);

    -- Collection = articles that have at least one keyword row. Documents
    -- with a missing/zero word_count are excluded from avgdl; at scoring time
    -- they get the average length (no length penalty).
    SELECT COUNT(*), AVG(n.word_count) FILTER (WHERE n.word_count > 0)
      INTO v_n, v_avgdl
    FROM (SELECT DISTINCT news_title, source, date FROM text.news_keywords) nk
    JOIN text.news n
      ON n.title = nk.news_title AND n.source = nk.source AND n.date = nk.date;

    IF COALESCE(v_n, 0) = 0 OR COALESCE(v_avgdl, 0) <= 0 THEN
        INSERT INTO text.bm25_corpus_stats (id, n_docs, avgdl, k1, b)
        VALUES (1, COALESCE(v_n, 0), COALESCE(v_avgdl, 0), v_k1, v_b)
        ON CONFLICT (id) DO UPDATE
            SET n_docs = EXCLUDED.n_docs, avgdl = EXCLUDED.avgdl,
                k1 = EXCLUDED.k1, b = EXCLUDED.b, updated_at = now();
        RETURN QUERY SELECT COALESCE(v_n, 0), COALESCE(v_avgdl, 0),
                            (SELECT COUNT(DISTINCT keyword)
                             FROM text.news_keywords)::INTEGER,
                            0::INTEGER;
        RETURN;
    END IF;

    -- Full recompute: O(keyword rows) per refresh, trivial at this corpus
    -- scale. count_pct-style raw TF is left untouched; bm25 is the weighted
    -- view of the same count.
    -- (The article join lives in WHERE: the ON clause of an UPDATE...FROM
    -- join may not reference the update target.)
    WITH df AS (
        SELECT keyword, COUNT(*) AS df
        FROM text.news_keywords
        GROUP BY keyword
    )
    UPDATE text.news_keywords nk
    SET bm25 = LN((v_n - kw.df + 0.5) / (kw.df + 0.5) + 1)
             * nk.count * (v_k1 + 1)
             / (nk.count + v_k1 *
                (1 - v_b + v_b * COALESCE(NULLIF(n.word_count, 0), v_avgdl)
                          / v_avgdl))
    FROM df kw, text.news n
    WHERE kw.keyword = nk.keyword
      AND n.title = nk.news_title AND n.source = nk.source AND n.date = nk.date;
    GET DIAGNOSTICS v_weighted = ROW_COUNT;

    INSERT INTO text.bm25_corpus_stats (id, n_docs, avgdl, k1, b)
    VALUES (1, v_n, v_avgdl, v_k1, v_b)
    ON CONFLICT (id) DO UPDATE
        SET n_docs = EXCLUDED.n_docs, avgdl = EXCLUDED.avgdl,
            k1 = EXCLUDED.k1, b = EXCLUDED.b, updated_at = now();

    RETURN QUERY SELECT v_n, v_avgdl,
                        (SELECT COUNT(DISTINCT keyword)
                         FROM text.news_keywords)::INTEGER,
                        v_weighted;
END;
$$;

COMMENT ON FUNCTION text.refresh_news_keyword_bm25() IS
  'Recompute text.bm25_corpus_stats (N, avgdl) and materialize the BM25 weight '
  'of every text.news_keywords row. Call after keyword ingestion; k1/b are '
  'taken from the stats singleton.';

-- Multi-term retrieval: the classic BM25 query score is the sum of per-term
-- weights, so ranking over the materialized column is a plain SUM.
CREATE OR REPLACE FUNCTION text.search_news_bm25(
    q_keywords TEXT[],
    max_rows   INTEGER DEFAULT 20)
RETURNS TABLE (news_title TEXT, source TEXT, pub_date DATE, score NUMERIC)
LANGUAGE sql
STABLE
AS $$
    SELECT nk.news_title, nk.source, nk.date, SUM(nk.bm25) AS score
    FROM text.news_keywords nk
    WHERE nk.keyword = ANY (q_keywords)
      AND nk.bm25 IS NOT NULL
    GROUP BY nk.news_title, nk.source, nk.date
    ORDER BY score DESC
    LIMIT LEAST(GREATEST(COALESCE(max_rows, 20), 1), 1000);
$$;

COMMENT ON FUNCTION text.search_news_bm25(TEXT[], INTEGER) IS
  'Rank articles by the summed BM25 weight of the query keywords. Weights come '
  'from the last text.refresh_news_keyword_bm25() run — refresh first after '
  'ingestion.';

-- ============================================================================
--  *** NOT IN USE — no embedding pipeline yet ***
--  Created empty for forward-compatibility. If the local BAAI/bge-m3 model
--  (1024 dims) is adopted, drop and recreate this still-empty table with
--  vector(1024) — no data migration needed.
-- ============================================================================
CREATE TABLE IF NOT EXISTS text.news_embeddings (
    news_title  TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    date        DATE    NOT NULL,
    embedding   vector(1536),               -- content embedding
    model       TEXT,                        -- embedding model id
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_embeddings PRIMARY KEY (news_title, source, date),
    CONSTRAINT fk_news_embeddings_news FOREIGN KEY (news_title, source, date)
        REFERENCES text.news (title, source, date)
);

COMMENT ON TABLE text.news_embeddings IS
  'Content embedding of each news article (vector(1536)) for cosine-similarity '
  'search (NOT IN USE yet). Join on text.news via (news_title, source, date).';

-- ANN index for cosine distance — query with the <=> operator.
CREATE INDEX IF NOT EXISTS ix_news_embeddings_cosine
    ON text.news_embeddings
    USING hnsw (embedding vector_cosine_ops);
