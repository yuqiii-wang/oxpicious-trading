-- MA-Spread "amt" extras group — the 5 trading-amount pairs' per-date
-- values: trading amount + high/low price reference + the 5 amt MA values
-- + their Bollinger σ columns (analysis.mov_ave_trading_amt), joined via
-- per-code prefetched MATERIALIZED CTEs. The per-sec_type CTE list, column
-- expressions and FROM joins are fragment args (SEC_SOURCES config).
-- Serves GET /api/analysis/mov-ave-spread/extras (metrics=amt).
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   codes: value text[] — the code variants to chart (codeVariants)
--   chart_ctes: fragment — chartCtesSql over src.chartTables + TRADING_AMT_TABLE
--   trading_amt_expr: fragment — src.tradingAmtExpr
--   high_expr: fragment — src.highExpr
--   low_expr: fragment — src.lowExpr
--   chart_joins: fragment — chartJoinSql over the same table set
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = {{sec_type}}
        AND code = ANY({{codes}}::text[])
    ),
    {{chart_ctes}}
    SELECT
      d.date,
      {{trading_amt_expr}} AS trading_amount,
      {{high_expr}} AS high,
      {{low_expr}} AS low,
      d.trading_amt_ma5, d.trading_amt_ma20, d.trading_amt_ma60,
      d.trading_amt_ma120, d.trading_amt_ma255,
      ta.trading_amt_std5, ta.trading_amt_std20, ta.trading_amt_std60,
      ta.trading_amt_std120, ta.trading_amt_std255
    FROM d
    {{chart_joins}}
    ORDER BY d.date ASC
