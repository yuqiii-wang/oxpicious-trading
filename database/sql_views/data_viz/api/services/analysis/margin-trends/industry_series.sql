-- Margin Trends 'industry' attribution — the industry's OWN margin curve,
-- ONE series per industry (code = industry_id, the sec_type=3 label
-- contract). ONE template for BOTH flavors (raw default vs the
-- ?normalize=true variant): DIRECT RETRIEVAL ONLY — the stored
-- margin_industry_stats columns come out as-is and every derived value is
-- computed consumer-side in margin-trends.ts from these raw columns:
--   margin_balance / margin_buy — per side COALESCE(a,0)+COALESCE(b,0)
--     NULLIF'd to NULL on an all-null/zero day so the line BREAKS instead
--     of dipping to 0; the curve reads the SUM(stock+etf) cols (raw) or
--     the *_norm member-AVERAGE cols (normalize=true; each AVG runs over
--     that side's members with a NON-NULL NON-ZERO rz_* on the date —
--     the two sides' averages are SUMMED, not averaged together);
--   n_members — members behind that date's averages (sum/avg per side,
--     BOTH flavors carry it; the member-count change dots render in raw
--     mode too) — per side COALESCE(ROUND(balance/NULLIF(norm,0))::int, 0):
--     a NULL/0 norm or NULL balance contributes 0 (norms are averages of
--     non-negative balances).
-- close/trading_amount come from the industry composite in
-- stats.industry_basic_stats (pool_size = 3 'all' slice); regime joins
-- stats.market_regimes sec_type=3 (the composite's own regime labels).
-- Serves GET /api/analysis/margin-trends/industry-series
-- (?attribution=industry, ?normalize=1|true)
-- args:
--   industry_id: value text — the industry whose own curve to fetch
--   regime_decode: fragment — labelDecodeSql("regime", "r.regime")
  SELECT mi.industry_id, mi.date,
         mi.stock_margin_balance, mi.etf_margin_balance,
         mi.stock_margin_buy, mi.etf_margin_buy,
         mi.stock_margin_balance_norm, mi.etf_margin_balance_norm,
         mi.stock_margin_buy_norm, mi.etf_margin_buy_norm,
         ib.mean_close AS close, ib.total_trading_amount AS trading_amount,
         mr.regime
  FROM analysis.margin_industry_stats mi
  LEFT JOIN LATERAL (
    SELECT mean_close, total_trading_amount
    FROM stats.industry_basic_stats b
    -- pool_size = 3 is the 'all' slice (static.label_dim 'pool_size')
    WHERE b.industry_id = mi.industry_id AND b.date = mi.date
      AND b.pool_size = 3
    LIMIT 1
  ) ib ON TRUE
  LEFT JOIN LATERAL (
    SELECT {{regime_decode}} AS regime
    FROM stats.market_regimes r
    -- sec_type stores its static.label_dim SMALLINT code (3 = industry)
    WHERE r.code = mi.industry_id AND r.date = mi.date AND r.sec_type = 3
    LIMIT 1
  ) mr ON TRUE
  WHERE mi.industry_id = {{industry_id}}::text
  ORDER BY mi.industry_id, mi.date
