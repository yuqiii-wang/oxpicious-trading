-- Classification for the sec_type='industry' ENTITY ids (code =
-- industry_id, e.g. BANKS / BROAD_CSI300): the securities classified under
-- each id grouped by industry_id — sector/label columns MIN-aggregated (an
-- id carries exactly one sector and label across its carriers) and
-- BOOL_AND(is_industry_not_strategy) picks the column. TTL-cached per
-- id-set in-process.
-- Serves the analysis theme services' industry-entity classification
-- (market-crowdedness / hot-stock-momentum / mov-ave-spreads themes).
-- args:
--   ids: value text[] — the industry entity ids (deduped, non-null)
      SELECT industry_id,
             MIN(sector_id)      AS sector_id,
             MIN(sector_label)   AS sector_label,
             MIN(industry_label) AS industry_label,
             MIN(industry_slug)  AS industry_slug,
             BOOL_AND(is_industry_not_strategy) AS is_industry_not_strategy
        FROM stats.sec_classification
       WHERE industry_id = ANY({{ids}}::text[])
       GROUP BY industry_id
