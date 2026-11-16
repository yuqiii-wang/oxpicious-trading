-- Market crowdedness hot-tech — one security's daily stats close with
-- the hot-stock tech aggregates LEFT JOINed from
-- analysis.market_crowdedness_hot_tech (per-(pool, date) mean + median
-- over the day's rolling hot set of eight technical metrics, each
-- aggregate also stored as a percentile vs the pool's own trailing-
-- 250-day history). Rows are driven by the close source so out-of-scope
-- codes still plot close only; metric fields are NULL on dates with no
-- hot-tech row (pre-gate days, percentile warm-up). The code predicate
-- is repeated inside the LEFT JOIN ON on purpose (the chart_series
-- pattern — without it the planner merge-joins by sorting the ENTIRE
-- metric table and degenerates).
-- args:
--   codes: value text[] — the code variants (bare + suffixed)
--   sec_type: value int — the static.label_dim sec_type code (1 index, 3 industry)
--   from_clause: fragment — the per-sec_type close source aliased to b (b.code / b.date / b.close)
    SELECT b.date,
           b.close,
           m.hot_n,
           m.mean_ma5_ratio,
           m.mean_ma5_ratio_pct,
           m.median_ma5_ratio,
           m.median_ma5_ratio_pct,
           m.mean_px_vs_ma20,
           m.mean_px_vs_ma20_pct,
           m.median_px_vs_ma20,
           m.median_px_vs_ma20_pct,
           m.mean_px_vs_ma60,
           m.mean_px_vs_ma60_pct,
           m.median_px_vs_ma60,
           m.median_px_vs_ma60_pct,
           m.mean_macd_hist,
           m.mean_macd_hist_pct,
           m.median_macd_hist,
           m.median_macd_hist_pct,
           m.mean_rsi14,
           m.mean_rsi14_pct,
           m.median_rsi14,
           m.median_rsi14_pct,
           m.mean_roc20,
           m.mean_roc20_pct,
           m.median_roc20,
           m.median_roc20_pct,
           m.mean_amt_vs_ma20,
           m.mean_amt_vs_ma20_pct,
           m.median_amt_vs_ma20,
           m.median_amt_vs_ma20_pct,
           m.mean_ret,
           m.mean_ret_pct,
           m.median_ret,
           m.median_ret_pct
    {{from_clause}}
    LEFT JOIN analysis.market_crowdedness_hot_tech m
      ON m.code = b.code AND m.date = b.date
      AND m.sec_type = {{sec_type}}
      AND m.code = ANY({{codes}}::text[])
    WHERE b.close IS NOT NULL
      AND b.code = ANY({{codes}}::text[])
    ORDER BY b.date ASC
