-- Margin Trends — industry_label probe for the 'etf' attribution:
-- recover the industry display name from the first ETF's
-- sec_classification row.
-- Serves GET /api/analysis/margin-trends/industry-series?attribution=etf
-- args:
--   code: value text — the first security's code (uncasted $1, pg infers
--     text from the comparison)
SELECT industry_label FROM stats.sec_classification
         WHERE code = {{code}} AND type = 'etf' AND is_active = TRUE LIMIT 1
