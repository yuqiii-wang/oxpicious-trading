-- Stock attribution step (analysis.market_beta internal pipeline) —
-- industry_id -> industry_label pairs for the aggregated frame's write
-- boundary (labels never ride the big source frame; they merge onto the
-- small (industry, date, bucket) result just before the DB write).
-- args: none
    SELECT DISTINCT industry_id, industry_label
    FROM stats.sec_classification
    WHERE type = 'stock'
      AND industry_id IS NOT NULL AND industry_id <> ''
      AND industry_label IS NOT NULL
