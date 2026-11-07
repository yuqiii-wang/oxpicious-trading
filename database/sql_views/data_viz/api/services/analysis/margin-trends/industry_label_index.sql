-- Margin Trends — industry_label probe for the 'index' attribution:
-- recover the industry display name from the first security's
-- sec_classification row.
-- Serves GET /api/analysis/margin-trends/industry-series?attribution=index
-- args:
--   code: value text — the first security's code (uncasted $1, pg infers
--     text from the comparison)
SELECT industry_label FROM stats.sec_classification
         WHERE code = {{code}} AND type = 'index' AND is_active = TRUE LIMIT 1
