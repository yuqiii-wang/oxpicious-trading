-- Pair Pct-Change Ratios — GET universe, UNSCOPED variant (no
-- industry_ids filter): both sides of the pair selector — subject
-- industries and benchmark industries, each with counterpart counts +
-- grid date bounds, stitched by UNION ALL (no pairwise fan-out; empty
-- filter → no scope fragments, full industry universe). Grain 1 rows only
-- (static.label_dim 'pair_grain'; the per-index grain was dropped in the
-- 2026-10-17 industry-vs-industry rework). The scoped variant (an
-- industry_ids filter present) is the sibling universe_scoped template.
-- Serves GET /api/analysis/pair-pct-ratios/universe
-- args:
--   stride: value int — the stride-grid interval
--   subject_label: fragment — INDUSTRY_LABEL_SQL("s.code")
--   benchmark_label: fragment — INDUSTRY_LABEL_SQL("b.code")
    WITH subjects AS (
      SELECT t.code AS code,
             COUNT(DISTINCT t.benchmark_code) AS n_counterparts,
             MIN(t.date) AS first_date,
             MAX(t.date) AS last_date
      FROM analysis.pair_pct_change_ratios t
      WHERE t."interval" = {{stride}} AND t.grain = 1 
      GROUP BY t.code
    ),
    benchmarks AS (
      SELECT t.benchmark_code AS code,
             COUNT(DISTINCT t.code) AS n_counterparts,
             MIN(t.date) AS first_date,
             MAX(t.date) AS last_date
      FROM analysis.pair_pct_change_ratios t
      WHERE t."interval" = {{stride}} AND t.grain = 1 
      GROUP BY t.benchmark_code
    )
    SELECT s.code, {{subject_label}}, s.n_counterparts,
           s.first_date, s.last_date, 'subject' AS kind
    FROM subjects s
    UNION ALL
    SELECT b.code, {{benchmark_label}}, b.n_counterparts,
           b.first_date, b.last_date, 'benchmark' AS kind
    FROM benchmarks b
