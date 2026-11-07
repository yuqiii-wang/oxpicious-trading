-- Perf-attr chart — active ETFs tracking one index
-- (sec_classification.parent_index_code). Used twice per request: ETFs
-- linked to the benchmark and (for index subjects) to the subject — for ETF
-- subjects the subject lookup returns nothing (the subject IS the ETF).
-- Serves GET /api/analysis/perf-attr/chart.
-- args:
--   parent_index_code: value text — the index whose tracking ETFs are listed
SELECT code, name FROM stats.sec_classification
       WHERE type = 'etf' AND is_active = TRUE AND parent_index_code = {{parent_index_code}}::text
       ORDER BY name NULLS LAST, code
