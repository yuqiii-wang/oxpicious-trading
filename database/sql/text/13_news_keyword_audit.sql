-- ============================================================================
--  text.news_keyword_audit — MATERIALIZED VIEW: the daily keyword audit
--  rollup over the seeded keyword vocabulary.
--
--  One row per (date, category_type, category_code): how many articles in
--  the text.news corpus mentioned that category's keywords that day, and
--  how many times in total. The category mapping is static.keyword_maps
--  (the seeded keyword → category map: 0 industry / 1 market-description /
--  2 broadmarket rows — market rows decode via static.label_dim 'kw_market',
--  broadmarket rows via 'kw_theme'; skip rows (3) are never extracted, so
--  no audit row carries them).
--
--  WHY MATERIALIZED: the Ask AI insight metrics for the Market Sentiments
--  page (analysis_insight_metrics page 'composites'/'ai-market-sentiments')
--  resolve as table-kind specs against this relation — value + 10y rarity
--  rank = two filtered aggregations per metric × every metric of the ask,
--  per modal open. A plain view re-joins the ~10^5-row text.news_keywords
--  per query; the matview answers from an index in ~ms. The underlying
--  keyword rows change ONLY when builds.text runs, which REFRESHes this
--  view (CONCURRENTLY) right after its keyword-row rewrite — never stale
--  w.r.t. its sources.
--
--  Refresh contract: builds.text (step 4, after recompute_keyword_stats)
--  runs REFRESH MATERIALIZED VIEW CONCURRENTLY text.news_keyword_audit.
--  Nothing else writes or refreshes it; manual DB edits to keyword rows
--  (there shouldn't be any) must re-run the refresh or a seed re-apply +
--  builds.text.
-- ============================================================================

-- Re-apply = DROP + CREATE: a matview definition change never propagates
-- through IF NOT EXISTS, and the data is derived (the build re-populates).
DROP MATERIALIZED VIEW IF EXISTS text.news_keyword_audit CASCADE;

CREATE MATERIALIZED VIEW text.news_keyword_audit AS
SELECT k.date,
       m.category_type,              -- 'kw_category_type': 0 industry | 1 market | 2 broadmarket | 3 skip (never extracted)
       m.category_code,              -- 'kw_market' when type=1, 'kw_theme' when type=2, NULL when industry or skip
       COUNT(DISTINCT k.news_id)::integer AS articles,  -- articles mentioning the category's keywords
       SUM(k.count)::bigint          AS mentions     -- total keyword occurrences across those articles
FROM text.news_keywords k
JOIN static.keyword_maps m USING (keyword)
GROUP BY k.date, m.category_type, m.category_code;

-- Row identity + the REFRESH CONCURRENTLY requirement.
CREATE UNIQUE INDEX IF NOT EXISTS uq_news_keyword_audit_date_category
    ON text.news_keyword_audit (date, category_type, category_code);

-- The insight metrics' access path: filter the category, walk dates desc
-- (value pick + the 10y prior-window rank scan).
CREATE INDEX IF NOT EXISTS ix_news_keyword_audit_category_date
    ON text.news_keyword_audit (category_type, category_code, date DESC);

COMMENT ON MATERIALIZED VIEW text.news_keyword_audit IS
  'Daily keyword-audit rollup over static.keyword_maps: per (date, '
  'category_type, category_code) how many text.news articles mentioned '
  'the category''s keywords (articles) and how many times (mentions). '
  'MATERIALIZED — refreshed by builds.text after its keyword-row rewrite; '
  'feeds the Market Sentiments page''s Ask AI insight metrics '
  '(composites/ai-market-sentiments).';

COMMENT ON COLUMN text.news_keyword_audit.articles IS
  'Distinct articles whose extracted keywords (text.news_keywords) include '
  'any keyword of the category that day — the breadth gauge the sentiment '
  'metrics resolve.';

COMMENT ON COLUMN text.news_keyword_audit.mentions IS
  'Total occurrences (SUM of text.news_keywords.count) of the category''s '
  'keywords across those articles.';

COMMENT ON COLUMN text.news_keyword_audit.category_code IS
  'Sub-type selected by category_type (static.label_dim): the ''kw_market'' '
  'code when category_type=1 (0=px_up 1=px_dn 2=sharp_up 3=sharp_dn 4=gap_up '
  '5=gap_dn 6=limit_up 7=limit_dn 8=rebound 9=pullback 10=vol_up 11=vol_dn '
  '12=divergence 13=bullish 14=bearish 15=volatile 16=mood 17=position '
  '18=break_up 19=break_dn), the ''kw_theme'' code '
  'when category_type=2, NULL for industry and skip rows '
  '(category_type=0/3).';
