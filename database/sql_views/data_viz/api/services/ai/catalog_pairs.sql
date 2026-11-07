-- AI QA feed — canonical industry catalog: DISTINCT decoded (sector,
-- industry) pairs from stats.sec_classification (industry-primary AND
-- strategy-primary rows alike); the scope resolution for the calendar,
-- items and QA reads filters through it.
-- Serves GET /api/ai/calendar + /api/ai/items + /api/ai/qa (10-min cache).
-- args: none
      SELECT DISTINCT COALESCE(sector_id, 'OTHER')   AS sector_id,
             COALESCE(industry_id, 'OTHER')          AS industry_id
        FROM stats.sec_classification
