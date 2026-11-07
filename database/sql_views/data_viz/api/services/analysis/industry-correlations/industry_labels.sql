-- Industry labels lookup — one row per industry_id with its first
-- non-empty industry_label (falling back to the bare id in the service).
-- Shared by the correlations service and the benchmark-offset
-- correlations service (fetchIndustryLabels).
-- Serves GET /api/analysis/industry-correlations and
-- GET /api/analysis/industry-corr-offsets.
-- args:
--   industry_ids: value text[] — the industry ids to label
SELECT industry_id, COALESCE(NULLIF(industry_label, ''), industry_id) AS industry_label
     FROM (
       SELECT DISTINCT industry_id, industry_label
       FROM stats.sec_classification
       WHERE type = 'index' AND industry_id = ANY({{industry_ids}}::text[])
     ) t
