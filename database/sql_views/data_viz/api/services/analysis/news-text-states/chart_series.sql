-- News Text States chart — the price context (close read live from
-- stats: the market scopes read the SSE Composite, industries read one
-- pool-'all' slice of stats.industry_basic_stats, sectors the
-- equal-weight mean of their member industries' slices — see the
-- service's CHART_FROM_CLAUSE) LEFT JOINed with the text.news_text_daily
-- daily panel (built by analysis.news_text_daily) and the RAW mood
-- layer of analysis.text_metrics_daily (built by analysis.text_metrics
-- — the merged-in Text Sentiment Metrics page's Tetlock z family).
-- The scope predicates are repeated inside the LEFT JOIN ON clauses on
-- purpose (the analysis.pe chart pattern): without them the planner
-- sorts the ENTIRE metric table and degenerates. Metric fields are NULL
-- on dates with no defined row (the n>=3 scored guard / window warm-up)
-- — the close still plots. Industry/sector-kind rows exist only on
-- event days.
-- Serves GET /api/analysis/news-text-states/chart
-- args:
--   from_clause: fragment — the scope's price source (see the service).
--     The industry/sector fragments filter the b subquery to the
--     requested scope and reference scope_code positionally as $2
--     (scope_kind is this template's first value slot → $1).
--   scope_kind: value int — label_dim 'news_scope_kind' code (0/1/2/3)
--   scope_code: value text — '000001' for the market kinds, the
--              industry_id for kind 2, the L1 sector_id for kind 3
--   raw_industry_id: value text — the raw layer's analysis.text_metrics_daily
--              industry_id key: '' (the broad-market row) for the market
--              kinds, the scope_code itself for kind 2, NULL for kind 3
--              (sectors have no raw rows — NULL never matches, so a
--              sector scope never silently falls back to market data)
    SELECT
      b.date,
      b.close,
      m.n_articles,
      m.n_scored,
      m.sent_level,
      m.sent_z,
      m.sent_ema3,
      m.sent_ema10,
      m.sent_delta,
      m.flow_af,
      m.flow_pos,
      m.flow_neg,
      m.disp_origin,
      m.lex_tone_z,
      m.low_coverage,
      r.sentiment_z,
      r.sentiment_ma5,
      r.flow_z,
      r.kw_tone
    {{from_clause}}
    LEFT JOIN text.news_text_daily m
      ON m.date = b.date
      AND m.scope_kind = {{scope_kind}}::int
      AND m.scope_code = {{scope_code}}::text
    LEFT JOIN analysis.text_metrics_daily r
      ON r.date = b.date
      AND r.industry_id = {{raw_industry_id}}::text
    WHERE b.close IS NOT NULL
    ORDER BY b.date
