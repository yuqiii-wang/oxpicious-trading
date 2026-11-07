-- Codes carrying at least one RSI row for the sec_type — the mov_rsi
-- universe pre-filter's RSI leg (a code without RSI data can never enter
-- a percentile bucket).
-- args:
--   rsi_table: fragment — analysis.mov_ave_rsi
--   sec_type: value int — the sec_type smallint code
SELECT DISTINCT code FROM {{rsi_table}} WHERE sec_type = {{sec_type}}
