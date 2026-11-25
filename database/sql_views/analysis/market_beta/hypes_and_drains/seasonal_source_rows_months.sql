-- Hypes & drains SEASONAL (monthly) aggregation — stored per-date ranking
-- rows of analysis.industry_hypes_and_drains for the MONTH-SCOPED
-- incremental re-aggregation: only the months that gained (re)computed
-- dates are fetched, re-ranked and re-inserted after their old seasonal
-- rows were purged (a month's peak/rank depends on ALL its stored
-- per-date rows, so the whole month is recomputed from the table — never
-- from the incremental slice alone). Only non-NULL metric_value rows
-- feed the monthly re-ranking (computed in pandas afterwards).
-- args:
--   season_qkeys: value text[] — 'YYYY-MM' month keys to fetch
    SELECT extract(epoch from date)::float8 AS date,
           benchmark_code, period_days, weighting, rank_side,
           industry_id, industry_label, metric_value::float8
           AS metric_value
    FROM analysis.industry_hypes_and_drains
    WHERE metric_value IS NOT NULL
      AND to_char(date, 'YYYY-MM') = ANY({{season_qkeys}}::text[])
