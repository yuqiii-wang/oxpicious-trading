-- Market crowdedness — per-stock daily technical inputs for the
-- hot-stock tech aggregates (analysis.market_crowdedness_hot_tech):
-- close (for the px-vs-MA / ROC gauges) + the precomputed tech columns
-- ma5_ratio / ma20 / ma60 / macd_hist, read from the reconstructed
-- stats.v_stock_daily view (identity + basic_stats + tech_stats +
-- liquidity_margin — we select only the needed columns). Rows with
-- none of the needed values drop out; the pipeline's left-merge leaves
-- NaN where the tech build lags a member-day.
-- args:
--   stock_codes: value text[] — the sorted member stock codes
--   lo: value date — the window's lower bound
--   hi: value date — the window's upper bound
    SELECT code AS stock_code,
           extract(epoch from date)::float8 AS date,
           close::float8 AS close,
           ma5_ratio::float8 AS ma5_ratio,
           ma20::float8 AS ma20,
           ma60::float8 AS ma60,
           macd_hist::float8 AS macd_hist
    FROM stats.v_stock_daily
    WHERE code = ANY({{stock_codes}}::text[])
      AND (close IS NOT NULL OR ma5_ratio IS NOT NULL
           OR ma20 IS NOT NULL OR ma60 IS NOT NULL
           OR macd_hist IS NOT NULL)
      AND date >= {{lo}}::date
      AND date <= {{hi}}::date
    ORDER BY code, date ASC
