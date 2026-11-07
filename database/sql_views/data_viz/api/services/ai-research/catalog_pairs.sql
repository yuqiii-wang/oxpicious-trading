-- AI research feed — canonical industry catalog: DISTINCT decoded (sector,
-- industry) pairs from stats.sec_classification; the scope resolution for
-- the research calendar / items / QA reads filters through it.
-- Serves GET /api/ai/research-calendar + /api/ai/research-items +
-- /api/ai/research-qa (10-min cache).
-- args: none
      SELECT DISTINCT COALESCE(sector_id, 'OTHER')   AS sector_id,
             COALESCE(industry_id, 'OTHER')          AS industry_id
        FROM stats.sec_classification
