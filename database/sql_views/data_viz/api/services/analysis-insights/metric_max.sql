-- Insight metric — the $latest dimension picker: MAX(dimension) over the
-- registry table under the scope filters. The table / column identifiers
-- come from analysis_insight_metrics.insight_templates at runtime (quoted
-- by the service's q() helper) and ride fragments; the predicates carry
-- the caller's shared $n args pool.
-- Serves GET /api/analysis-insights/metrics (the $latest match dims).
-- args:
--   table: fragment — the quoted registry table identifier
--   metric_key: fragment — the quoted $latest dimension column identifier
--   where_clause: fragment — "WHERE <scope predicates joined AND>" / ""
--     (caller-built; carries the caller's $n args)
SELECT MAX({{metric_key}}) AS m
  FROM {{table}}
  {{where_clause}}
