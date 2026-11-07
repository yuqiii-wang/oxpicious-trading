-- Margin Trends — sustained UP/DOWN trend episodes for the 'etf'
-- attribution: analysis.margin_changes rows (sec_type = 0, the
-- static.label_dim 'etf' code) for the industry's member ETF codes,
-- ordered by episode start.
-- Serves GET /api/analysis/margin-trends/trends?attribution=etf
-- args:
--   industry_id: value text — the industry whose member episodes to list
  WITH industry_codes AS (
    SELECT sc.code
    FROM stats.sec_classification sc
    WHERE sc.type = 'etf'
      AND sc.industry_id = {{industry_id}}::text
      AND sc.parent_index_is_primary = TRUE
      AND sc.is_active = TRUE
  )
  SELECT t.code, t.start_date, t.end_date, t.is_trend_up_not_down,
         t.rz_buy_vs_trading_amt_ratio
  FROM analysis.margin_changes t
  JOIN industry_codes ic ON ic.code = t.code
  WHERE t.sec_type = 0 -- static.label_dim 'sec_type': etf
  ORDER BY t.start_date
