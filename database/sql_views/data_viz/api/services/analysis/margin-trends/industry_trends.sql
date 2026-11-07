-- Margin Trends — sustained UP/DOWN trend episodes for the 'industry'
-- attribution: analysis.margin_changes rows (sec_type = 3, the
-- static.label_dim 'industry' code; code = industry_id — the composite's
-- own episodes), ordered by episode start.
-- Serves GET /api/analysis/margin-trends/trends?attribution=industry
-- args:
--   industry_id: value text — the industry whose episodes to list
  SELECT t.code, t.start_date, t.end_date, t.is_trend_up_not_down,
         t.rz_buy_vs_trading_amt_ratio
  FROM analysis.margin_changes t
  WHERE t.code = {{industry_id}}::text
    AND t.sec_type = 3 -- static.label_dim 'sec_type': industry
  ORDER BY t.start_date
