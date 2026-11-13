-- MA-Spread DEFAULT chart best-effort shared block — the per-date tooltip
-- metrics: the trading-amount table's amt slopes / market shares
-- (analysis.mov_ave_trading_amt — the amt metrics' single authoritative
-- home since the 2026-11-08 detail split) + the RSI table (Wilder RSI +
-- tail-episode typicality flags, analysis.mov_ave_rsi +
-- analysis.trend_episode_days) + the MATERIALIZED MACD block (the
-- per-sec_type stats.*_tech_stats columns via the prefetched `t` CTE —
-- the 2026-11-09 real-run round) + the shared-tooltip OHLC pivot (4
-- windows a pair's ma_long can take, from the roof/floor lines table).
-- No other stats price tables — sec_type binds through the sec_type
-- parameter. The CTE list, OHLC pivot and FROM joins are fragment args.
-- Serves GET /api/analysis/mov-ave-spread/chart/shared (and /chart).
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   codes: value text[] — the code variants to chart (codeVariants)
--   chart_ctes: fragment — chartCtesSql over the rsi analysis table + the
--     per-sec_type tech_stats source (alias `t`)
--   ohlc_ctes: fragment — ohlcPivotCteSql(OHLC_SHARED_PERIODS, OHLC_SHARED_STEMS)
--   ohlc_select: fragment — ohlcPivotSelectSql(OHLC_SHARED_PERIODS, "sh_", OHLC_SHARED_STEMS)
--   chart_joins: fragment — chartJoinSql over the rsi analysis table + the
--     tech_stats source
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_trading_amt
      WHERE sec_type = {{sec_type}}
        AND code = ANY({{codes}}::text[])
    ),
    {{chart_ctes}},
    {{ohlc_ctes}}
    SELECT
      d.date,
      d.trading_amt_ma5_slope, d.trading_amt_ma20_slope, d.trading_amt_ma60_slope,
      d.trading_amt_ma120_slope, d.trading_amt_ma252_slope,
      d.trading_amt_market_share_ma5, d.trading_amt_market_share_ma20,
      d.trading_amt_market_share_ma60, d.trading_amt_market_share_ma120,
      d.trading_amt_market_share_ma252,
      rsi.rsi_3days, rsi.rsi_6days, rsi.rsi_10days, rsi.rsi_14days, rsi.rsi_20days,
      rsi.rsi_60days,
      t.macd_line::float8, t.macd_signal::float8, t.macd_hist::float8,
      ted.is_typical_3days, ted.is_typical_6days, ted.is_typical_10days,
      ted.is_typical_14days, ted.is_typical_20days, ted.is_typical_60days,
      {{ohlc_select}}
    FROM d
    {{chart_joins}}
    LEFT JOIN analysis.trend_episode_days ted
      ON ted.sec_type = d.sec_type AND ted.code = d.code AND ted.date = d.date
    LEFT JOIN ohlc ON ohlc.code = d.code AND ohlc.date = d.date
    ORDER BY d.date ASC
