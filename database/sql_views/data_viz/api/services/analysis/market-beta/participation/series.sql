-- Market Beta page — "Market Participation" panel series: the latest N
-- trading days of analysis.market_participation (whole-market turnover,
-- SSE share, advance/decline breadth), oldest first for the chart
-- date axis.
-- args:
--   days: value int — how many latest trading days to return (default 260)
SELECT date,
       total_trading_amount,
       ss_trading_amount,
       sz_trading_amount,
       total_amt_vs_ma5,
       total_amt_vs_ma20,
       total_amt_vs_ma60,
       total_amt_pct_rank_252,
       ss_share,
       ss_share_ma5,
       ss_share_ma20,
       ss_share_ma60,
       ss_share_vs_ma20_pp,
       ss_share_slope20_pp,
       adv_count,
       dec_count,
       flat_count,
       ad_ratio,
       median_pct_change,
       mean_pct_change,
       ad_ratio_ma5,
       ad_ratio_ma20,
       ad_ratio_pct_rank_252
  FROM analysis.market_participation
 WHERE date IN (
       SELECT date
         FROM analysis.market_participation
        ORDER BY date DESC
        LIMIT {{days}}::int
 )
 ORDER BY date
