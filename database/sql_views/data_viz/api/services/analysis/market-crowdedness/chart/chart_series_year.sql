-- Market Crowdedness chart, year-pinned variant — one security's
-- daily stats close + trading amount with the PINNED crowdedness metric
-- LEFT JOINed from analysis.market_crowdedness_year (the same
-- percentile-normalized multi-pillar contract as the rolling chart, but
-- the hot basket is frozen to the selected year's roster — see
-- analysis.market_crowdedness_hot_sets). The pinned variants store the
-- pool's WHOLE date span (the percentile windows need the trailing
-- history), but the API serves ONE CALENDAR YEAR per hot_year —
-- "2020" = 2020-01-01..2020-12-31, "2021" = its own year, counted
-- forward from the data start, never a trailing window ending at the
-- latest date. The code predicate is repeated inside the LEFT JOIN ON
-- clause on purpose (the analysis.pe chart pattern): without it the
-- planner merge-joins by sorting the ENTIRE metric table and
-- degenerates. The close/amount source varies by sec_type (the same
-- sources analysis.pe reads).
-- Serves GET /api/analysis/market-crowdedness/chart?hot_year=…
-- args:
--   codes: value text[] — codeVariants(target) (bare + suffixed forms)
--   sec_type: value smallint — LABEL_CODE.sec_type[secType]
--   hot_year: value int — the calendar year (validated upstream)
--   from_clause: fragment — CHART_FROM_CLAUSE[secType]
    SELECT
      b.date,
      b.close,
      b.trading_amount,
      m.crowdedness_score,
      m.crowdedness_score_ma5,
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
    LEFT JOIN analysis.market_crowdedness_year m
      ON m.code = b.code AND m.date = b.date AND m.sec_type = {{sec_type}}
      AND m.hot_year = {{hot_year}}::int
      AND m.code = ANY({{codes}}::text[])
    WHERE b.close IS NOT NULL
      AND b.code = ANY({{codes}}::text[])
      AND b.date >= make_date({{hot_year}}::int, 1, 1)
      AND b.date < make_date({{hot_year}}::int + 1, 1, 1)
    ORDER BY b.date ASC
