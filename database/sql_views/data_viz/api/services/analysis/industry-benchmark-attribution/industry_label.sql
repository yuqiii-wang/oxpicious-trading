-- Benchmark Attribution — the industry display label (all distinct
-- industry_label values for ONE industry; first row wins client-side).
-- Serves GET /api/analysis/industry-benchmark-attribution.
-- args:
--   industry_id: value text — the industry whose label to fetch
SELECT DISTINCT industry_label
     FROM stats.sec_classification
     WHERE type = 'index' AND industry_id = {{industry_id}}::text
