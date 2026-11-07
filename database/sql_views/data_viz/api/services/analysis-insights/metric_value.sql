-- Insight metric — the per-date latest value (one row per date: the
-- value-max within the date partition), the two most recent dates first
-- (value + previous for the delta). The table / column identifiers come
-- from analysis_insight_metrics.insight_templates at runtime (quoted by
-- the service's q() helper) and ride fragments; the predicates carry the
-- caller's shared $n args pool.
-- Serves GET /api/analysis-insights/metrics (the value + prev_value).
-- args:
--   table: fragment — the quoted registry table identifier
--   date_col: fragment — the quoted registry date column identifier
--   value_col: fragment — the quoted registry value column identifier
--   order_alias: fragment — "v" (value-ordered) / "d" (date-ordered) per
--     the spec's order.by
--   order_dir: fragment — "ASC" / "DESC" per the spec's order.dir
--   where_clause: fragment — "WHERE <scope predicates joined AND>" / ""
--     (caller-built; carries the caller's $n args)
SELECT d, v
  FROM (
    SELECT {{date_col}} AS d, {{value_col}} AS v,
           ROW_NUMBER() OVER (PARTITION BY {{date_col}}
                              ORDER BY {{value_col}} DESC NULLS LAST) AS rn
      FROM {{table}}
      {{where_clause}}
  ) t
  WHERE rn = 1 AND v IS NOT NULL
  ORDER BY {{order_alias}} {{order_dir}} NULLS LAST, d DESC
  LIMIT 2
