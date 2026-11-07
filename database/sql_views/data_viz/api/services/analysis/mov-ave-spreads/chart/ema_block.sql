-- MA-Spread DEFAULT chart best-effort EMA block — the EMA detail table
-- (gaps/derivatives/σ) plus the price columns the EMA pair rows carry,
-- joined via per-code prefetched MATERIALIZED CTEs. No RSI, no OHLC
-- pivot. The per-sec_type CTE list, price column expressions and FROM
-- joins are fragment args (SEC_SOURCES config; the ema analysis table
-- appends to the chartTables).
-- Serves GET /api/analysis/mov-ave-spread/chart/ema (and /chart).
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   codes: value text[] — the code variants to chart (codeVariants)
--   chart_ctes: fragment — chartCtesSql over src.chartTables + the ema detail table
--   price_expr: fragment — src.priceExpr
--   open_expr: fragment — src.openExpr
--   high_expr: fragment — src.highExpr
--   low_expr: fragment — src.lowExpr
--   trading_amt_expr: fragment — src.tradingAmtExpr
--   chart_joins: fragment — chartJoinSql over the same table set
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = {{sec_type}}
        AND code = ANY({{codes}}::text[])
    ),
    {{chart_ctes}}
    SELECT
      d.date,
      {{price_expr}} AS price,
      {{open_expr}} AS open,
      {{high_expr}} AS high,
      {{low_expr}} AS low,
      {{trading_amt_expr}} AS trading_amount,
      t.ema6, t.ema12, t.ema26,
      d.price_slope, d.price_curvature,
      ema.ema6_slope, ema.ema12_slope,
      ema.ema6_curvature, ema.ema12_curvature,
      ema.price_vs_ema6, ema.price_vs_ema12, ema.price_vs_ema26,
      ema.ema6_vs_ema12, ema.ema6_vs_ema26, ema.ema12_vs_ema26,
      ema.std_5days AS ema_std_5days,
      ema.std_12days AS ema_std_12days,
      ema.std_26days AS ema_std_26days
    FROM d
    {{chart_joins}}
    ORDER BY d.date ASC
