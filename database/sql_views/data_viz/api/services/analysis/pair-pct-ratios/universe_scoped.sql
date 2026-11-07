-- Pair Pct-Change Ratios — GET universe, SCOPED variant (an industry_ids
-- filter present): both sides of the pair selector scoped to those
-- industry_ids — the page scopes the selector to its already-selected
-- industries. Same shape as the sibling universe template with the scope
-- predicates inlined; the same industry_ids array binds both sides.
-- Grain 1 rows only (static.label_dim 'pair_grain').
-- Serves GET /api/analysis/pair-pct-ratios/universe
-- args:
--   stride: value int — the stride-grid interval
--   industry_ids: value text[] — the industry_ids scoping BOTH sides
--   subject_label: fragment — INDUSTRY_LABEL_SQL("s.code")
--   benchmark_label: fragment — INDUSTRY_LABEL_SQL("b.code")
    WITH subjects AS (
      SELECT t.code AS code,
             COUNT(DISTINCT t.benchmark_code) AS n_counterparts,
             MIN(t.date) AS first_date,
             MAX(t.date) AS last_date
      FROM analysis.pair_pct_change_ratios t
      WHERE t."interval" = {{stride}} AND t.grain = 1 AND t.code = ANY({{industry_ids}}::text[])
      GROUP BY t.code
    ),
    benchmarks AS (
      SELECT t.benchmark_code AS code,
             COUNT(DISTINCT t.code) AS n_counterparts,
             MIN(t.date) AS first_date,
             MAX(t.date) AS last_date
      FROM analysis.pair_pct_change_ratios t
      WHERE t."interval" = {{stride}} AND t.grain = 1 AND t.benchmark_code = ANY({{industry_ids}}::text[])
      GROUP BY t.benchmark_code
    )
    SELECT s.code, {{subject_label}}, s.n_counterparts,
           s.first_date, s.last_date, 'subject' AS kind
    FROM subjects s
    UNION ALL
    SELECT b.code, {{benchmark_label}}, b.n_counterparts,
           b.first_date, b.last_date, 'benchmark' AS kind
    FROM benchmarks b
