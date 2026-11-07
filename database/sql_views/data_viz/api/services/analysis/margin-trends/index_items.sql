-- Margin Trends — securities per industry for the 'index' attribution:
-- distinct index_code from the analysis.margin_index_series TABLE with
-- their sec_classification name labels. The DISTINCT runs BEFORE the
-- sec_classification LATERAL name lookup: deduping first gives ~200
-- lateral probes instead of one per raw series row (~325k).
-- Serves GET /api/analysis/margin-trends/themes
-- args: (none)
  WITH distinct_codes AS (
    SELECT DISTINCT industry_id, index_code
    FROM analysis.margin_index_series
    WHERE industry_id IS NOT NULL AND industry_id <> ''
  )
  SELECT d.industry_id,
         d.index_code AS code,
         COALESCE(NULLIF(sc.name, ''), d.index_code) AS name
  FROM distinct_codes d
  LEFT JOIN LATERAL (
    SELECT name FROM stats.sec_classification
    WHERE code = d.index_code AND type = 'index' AND is_active = TRUE
    LIMIT 1
  ) sc ON TRUE
  ORDER BY d.index_code
