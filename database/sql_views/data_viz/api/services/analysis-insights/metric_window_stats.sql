-- Insight metric — the past-60d window stats: the OPEN (earliest), MAX
-- and MIN of the metric's own prior series (same scope filters, one row
-- per date, the same rn=1 dedupe as metric_value) over the trailing
-- window (60 observations) STRICTLY PRIOR to the value's own date — the
-- basis of the delta_60d_* family (value − each stat). Mirrors the
-- window contract of the 01_ DDL header and the python resolver's
-- inline twin. Table/column identifiers come from
-- analysis_insight_metrics.insight_templates at runtime (quoted by the
-- service's q() helper) and ride fragments; the predicates carry the
-- caller's shared $n args pool.
-- Serves GET /api/analysis-insights/metrics (win_* / delta_60d_*).
-- args:
--   table: fragment — the quoted registry table identifier
--   date_col: fragment — the quoted registry date column identifier
--   value_col: fragment — the quoted registry value column identifier
--   win_where: fragment — "WHERE <prior-window scope predicates joined
--     AND>" (caller-built; carries the caller's $n args — must include
--     the strictly-prior bound date_col < value_date)
--   limit: fragment — the window size as a clamped int literal (60; a
--     positional $n param is NOT inferable in LIMIT position — the
--     param-bearing-fragment pattern's literal rule)
SELECT (ARRAY_AGG(v ORDER BY d ASC))[1] AS win_open,
       MAX(v) AS win_max,
       MIN(v) AS win_min,
       COUNT(*)::int AS win_n
  FROM (
    SELECT d, v
      FROM (
        SELECT {{date_col}} AS d, {{value_col}} AS v,
               ROW_NUMBER() OVER (PARTITION BY {{date_col}}
                                  ORDER BY {{value_col}} DESC NULLS LAST) AS rn
          FROM {{table}}
          {{win_where}}
      ) t
      WHERE rn = 1 AND v IS NOT NULL
      ORDER BY d DESC
      LIMIT {{limit}}
  ) w
