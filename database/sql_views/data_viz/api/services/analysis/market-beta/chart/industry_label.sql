-- Market-beta chart — the industry display label (one row's
-- industry_label; empty response falls back to "" client-side).
-- Serves GET /api/analysis/market-beta chart.
-- args:
--   industry_id: value text — the industry whose label to fetch
SELECT industry_label FROM stats.sec_classification
       WHERE industry_id = {{industry_id}}::text AND type = 'index' LIMIT 1
