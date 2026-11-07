-- Hypes & drains step (analysis.market_beta internal pipeline) —
-- industry_id -> industry_label pairs (the former industry_label CTE),
-- left-joined in pandas; a duplicated pair fans out identically to the
-- former final-SELECT join.
-- args: none
    SELECT DISTINCT industry_id, industry_label
    FROM stats.sec_classification
    WHERE type = 'index' AND industry_id IS NOT NULL AND industry_id <> ''
      AND industry_label IS NOT NULL
      AND is_industry_not_strategy = TRUE
