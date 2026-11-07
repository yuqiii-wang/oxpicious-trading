-- PE chart — one asset's daily close + pe + dividend_yield series, driven
-- from the sec_type-appropriate close source (fragments) and LEFT JOINed
-- with analysis.pe and analysis.dividends so the response keeps one row per
-- close day with NULLs where a metric is undefined.
--
-- The code predicate is repeated inside BOTH LEFT JOIN ON clauses on
-- purpose: without it the planner merge-joins by sorting the ENTIRE
-- analysis.pe / analysis.dividends tables and degenerates into a
-- near-cartesian product (measured 2.67M rows / 30s for ONE index code,
-- 2026-09-29); with it each side becomes an index probe. The ETF
-- chart_from_clause fragment repeats $1 for the same reason on
-- etf_adjustment — the fragment's literal $1 is the same bound array as
-- the {{codes}} slot.
-- Serves GET /api/analysis/pe/chart.
-- args:
--   codes: value text[] — the code's variants (codeVariants); also
--     referenced literally as $1 inside the ETF chart_from_clause fragment
--   close_expr: fragment — SEC_SOURCES[sec_type].closeExpr
--   chart_from_clause: fragment — SEC_SOURCES[sec_type].chartFromClause
    SELECT
      b.date,
      {{close_expr}} AS close,
      p.pe,
      dy.dividend_yield
    {{chart_from_clause}}
    LEFT JOIN analysis.pe p
      ON p.code = b.code AND p.date = b.date AND p.code = ANY({{codes}}::text[])
    LEFT JOIN analysis.dividends dy
      ON dy.code = b.code AND dy.date = b.date AND dy.code = ANY({{codes}}::text[])
    WHERE b.close IS NOT NULL
      AND b.code = ANY({{codes}}::text[])
    ORDER BY b.date ASC
