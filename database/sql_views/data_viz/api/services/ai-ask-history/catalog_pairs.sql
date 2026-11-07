-- AI ask-history — canonical industry catalog: DISTINCT decoded (sector,
-- industry) pairs from stats.sec_classification; resolves one L1 sector
-- pick's industry set for the ask feed's scope filters.
-- Serves GET /api/ai/ask-calendar + /api/ai/ask-items (cached).
-- args: none
SELECT DISTINCT COALESCE(sector_id, 'OTHER')   AS sector_id,
                  COALESCE(industry_id, 'OTHER')          AS industry_id
             FROM stats.sec_classification
