-- Margin Trends — sustained UP/DOWN trend episodes for the 'index'
-- attribution: analysis.margin_changes rows (sec_type = 1, the
-- static.label_dim 'index' code) for the industry's member index codes,
-- ordered by episode start. Drives the light shade (markArea) over each
-- trend window on the series plot.
-- Serves GET /api/analysis/margin-trends/trends?attribution=index
-- args:
--   industry_id: value text — the industry whose member episodes to list
  WITH industry_codes AS (
    SELECT DISTINCT index_code AS code
    FROM analysis.margin_index_series
    WHERE industry_id = {{industry_id}}::text
  )
  SELECT t.code, t.start_date, t.end_date, t.is_trend_up_not_down,
         t.rz_buy_vs_trading_amt_ratio
  FROM analysis.margin_changes t
  JOIN industry_codes ic ON ic.code = t.code
  WHERE t.sec_type = 1 -- static.label_dim 'sec_type': index
  ORDER BY t.start_date
