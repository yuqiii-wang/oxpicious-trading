-- MA-Spread DEFAULT chart REQUIRED pairs block — the stats price/MA tables
-- joined (via per-code prefetched MATERIALIZED CTEs, the optimization
-- fence) to the detail table's 9 Simple-MA gaps / derivatives / σ columns.
-- No EMA detail, no RSI, no OHLC pivot. The per-sec_type CTE list, price
-- column expressions and FROM joins are fragment args (SEC_SOURCES config).
-- Serves GET /api/analysis/mov-ave-spread/chart/pairs (and /chart).
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   codes: value text[] — the code variants to chart (codeVariants)
--   chart_ctes: fragment — chartCtesSql(src.chartTables) WITH-CTE list
--   price_expr: fragment — src.priceExpr
--   open_expr: fragment — src.openExpr
--   high_expr: fragment — src.highExpr
--   low_expr: fragment — src.lowExpr
--   trading_amt_expr: fragment — src.tradingAmtExpr
--   chart_joins: fragment — chartJoinSql(src.chartTables) FROM join list
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
      t.ma5, t.ma20, t.ma60, t.ma120, t.ma255,
      d.price_vs_ma5, d.price_vs_ma20, d.price_vs_ma60,
      d.price_vs_ma120, d.price_vs_ma255,
      d.ma5_vs_ma20, d.ma5_vs_ma60, d.ma5_vs_ma120, d.ma5_vs_ma255,
      d.price_slope, d.ma5_slope, d.ma20_slope,
      d.price_curvature, d.ma5_curvature, d.ma20_curvature,
      d.std_5days, d.std_20days, d.std_60days, d.std_120days, d.std_255days
    FROM d
    {{chart_joins}}
    ORDER BY d.date ASC
