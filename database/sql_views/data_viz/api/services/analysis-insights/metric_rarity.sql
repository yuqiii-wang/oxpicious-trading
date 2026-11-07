-- Insight metric — the value's rarity rank: its percentile within the
-- PRIOR trailing history (same scope filters, one row per date, the 10y
-- window ending at the value's own date). The boolean cast (not a CASE)
-- yields the 0/1 weights — the outer v IS NOT NULL guard keeps NULL v out
-- of the average, matching the retired CASE's ELSE branch. Table/column
-- identifiers come from analysis_insight_metrics.insight_templates at
-- runtime (quoted by the service's q() helper) and ride fragments; the
-- predicates carry the caller's shared $n args pool.
-- Serves GET /api/analysis-insights/metrics (pct_rank / pct_rank_n).
-- args:
--   table: fragment — the quoted registry table identifier
--   date_col: fragment — the quoted registry date column identifier
--   value_col: fragment — the quoted registry value column identifier
--   le_pred: fragment — "v <= $n" (the value's less-equal predicate)
--   rank_where: fragment — "WHERE <prior-window scope predicates joined
--     AND>" (caller-built; carries the caller's $n args)
SELECT AVG(({{le_pred}})::int::numeric) AS pct_rank,
       COUNT(*)::int AS n
  FROM (
    SELECT {{date_col}} AS d, {{value_col}} AS v,
           ROW_NUMBER() OVER (PARTITION BY {{date_col}}
                              ORDER BY {{value_col}} DESC NULLS LAST) AS rn
      FROM {{table}}
      {{rank_where}}
  ) t
  WHERE rn = 1 AND v IS NOT NULL
