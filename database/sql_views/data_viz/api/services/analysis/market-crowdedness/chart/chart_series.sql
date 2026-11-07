-- Market Crowdedness chart — one security's daily stats close + trading
-- amount with the crowdedness metric LEFT JOINed from
-- analysis.market_crowdedness (percentile-normalized multi-pillar
-- crowdedness_score with its four RAW pillars — concentration /
-- flow_change / comovement / valuation — their *_pct history percentiles
-- plus the hot-set names as tooltip evidence). The code predicate is
-- repeated inside the LEFT JOIN ON clause on purpose (the analysis.pe
-- chart pattern): without it the planner merge-joins by sorting the
-- ENTIRE metric table and degenerates. The close/amount source varies by
-- sec_type (the same sources analysis.pe reads).
-- Serves GET /api/analysis/market-crowdedness/chart
-- args:
--   codes: value text[] — codeVariants(target) (bare + suffixed forms)
--   sec_type: value smallint — LABEL_CODE.sec_type[secType]
--   from_clause: fragment — CHART_FROM_CLAUSE[secType]
    SELECT
      b.date,
      b.close,
      b.trading_amount,
      m.crowdedness_score,
      m.concentration,
      m.flow_change,
      m.comovement,
      m.valuation,
      m.top1_share,
      m.top5_share,
      m.top10_share,
      m.effective_n,
      m.concentration_pct,
      m.flow_pct,
      m.comovement_pct,
      m.valuation_pct,
      m.top_stocks
    {{from_clause}}
    LEFT JOIN analysis.market_crowdedness m
      ON m.code = b.code AND m.date = b.date AND m.sec_type = {{sec_type}}
      AND m.code = ANY({{codes}}::text[])
    WHERE b.close IS NOT NULL
      AND b.code = ANY({{codes}}::text[])
    ORDER BY b.date ASC
