-- Industry Corr Offsets coverage — the selectable industry list for the
-- page's multi-select: distinct type='index' industries from
-- stats.sec_classification, flagged with whether the offsets table has
-- any materialized row for them (covering BOTH pair endpoints, so an
-- industry that only ever appears as the lexicographically LARGER
-- endpoint is still flagged has_rows). Inner DISTINCTs dedupe each side
-- BEFORE the UNION so the pass stays in hash memory.
-- Serves GET /api/analysis/industry-corr-offsets/industries.
-- args: none
    WITH cov AS (
      SELECT DISTINCT industry_id AS ind
      FROM analysis_composites.industry_corr_benchmark_offsets
      UNION
      SELECT DISTINCT benchmark_industry_id
      FROM analysis_composites.industry_corr_benchmark_offsets
    )
    SELECT
      sc.industry_id,
      COALESCE(NULLIF(sc.industry_label, ''), sc.industry_id) AS industry_label,
      (c.ind IS NOT NULL) AS has_rows
    FROM (
      SELECT DISTINCT industry_id, industry_label
      FROM stats.sec_classification
      WHERE type = 'index'
        AND industry_id IS NOT NULL
        AND industry_id <> ''
    ) sc
    LEFT JOIN cov c ON c.ind = sc.industry_id
    ORDER BY sc.industry_id
