-- The display label of ONE industry from the INDEX classification (same
-- taxonomy as the composition industry_ids). Serves
-- GET /api/sec-composition/industry-series.
-- args:
--   industry_id: value text — the industry to label
SELECT industry_label FROM stats.sec_classification
      WHERE industry_id = {{industry_id}}::text AND type = 'index' LIMIT 1
