-- Hot Stock Momentum chart — one security's daily stats close + trading
-- amount with the hot-stock momentum metric LEFT JOINed from
-- analysis.hot_stock_momentum (NULL on every row until the placeholder
-- pipeline lands). The code predicate is repeated inside the LEFT JOIN ON
-- clause on purpose (the analysis.pe chart pattern): without it the
-- planner merge-joins by sorting the ENTIRE metric table and degenerates.
-- The close/amount source varies by sec_type (the same sources analysis.pe
-- reads).
-- Serves GET /api/analysis/hot-stock-momentum/chart
-- args:
--   codes: value text[] — codeVariants(target) (bare + suffixed forms)
--   sec_type: value smallint — LABEL_CODE.sec_type[secType]
--   from_clause: fragment — CHART_FROM_CLAUSE[secType]
    SELECT
      b.date,
      b.close,
      b.trading_amount,
      m.momentum
    {{from_clause}}
    LEFT JOIN analysis.hot_stock_momentum m
      ON m.code = b.code AND m.date = b.date AND m.sec_type = {{sec_type}}
      AND m.code = ANY({{codes}}::text[])
    WHERE b.close IS NOT NULL
      AND b.code = ANY({{codes}}::text[])
    ORDER BY b.date ASC
