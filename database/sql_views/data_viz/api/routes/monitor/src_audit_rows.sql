-- Source-data audit rows — per-security coverage from
-- monitor.src_data_audit, each code's last_date re-derived at query time
-- from the stats source tables (the served types' live CTEs ride caller
-- fragments — their per-type sec_type guards $5.. are variable-arity, so
-- the params stay caller-assembled; $1..$4 are the fixed filter/limit
-- params the route binds itself). Codes with no live rows fall back to
-- the stored audit value; q narrows by code substring or display name
-- (name JOINed in from stats.sec_classification — one row per code,
-- primary-parent row preferred).
-- Serves GET /api/monitor/src-data-audit (the rows block; counts are
-- src_audit_counts).
-- args:
--   live_ctes: fragment — the served types' last_date CTE definitions
--     joined ", " (e.g. "li AS (...), le AS (...)")
--   last_date_expr: fragment — "COALESCE(li.last_date, ..., 
--     a.last_date::text)" over the served CTE names
--   live_joins: fragment — the served types' LEFT JOINs, each guarded by
--     its own $n sec_type param (bare 6-digit codes exist in BOTH the
--     index and ETF identity tables — an unguarded join could cross-match)
WITH {{live_ctes}}
SELECT a.sec_type, a.code, a.exchange, a.board, a.n_days,
       a.first_date::text AS first_date,
       {{last_date_expr}} AS last_date,
       a.updated_at, sc.name
FROM monitor.src_data_audit a
LEFT JOIN LATERAL (
     SELECT s.name
       FROM stats.sec_classification s
      WHERE s.code = a.code AND s.type = $2::text
      ORDER BY s.parent_index_is_primary DESC
      LIMIT 1
) sc ON TRUE
{{live_joins}}
WHERE ($1::smallint IS NULL OR a.sec_type = $1)
  AND ($3::text = '' OR a.code ILIKE '%' || $3 || '%'
                  OR sc.name ILIKE '%' || $3 || '%')
ORDER BY a.sec_type, a.code
LIMIT $4
