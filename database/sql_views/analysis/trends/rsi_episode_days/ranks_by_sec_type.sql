-- trends rsi_episode_days — the RSI rank columns for one
-- sec_type from analysis.mov_ave_rsi_ranks (the ranks hook's
-- just-rebuilt frame; the episode-days compute consumes it).
-- args:
--   rank_columns: fragment — the rank_rsi_{W}days select list
--       (", ".join over RSI_WINDOWS)
--   sec_type: value smallint — the sec_type label code
SELECT sec_type, code, date, {{rank_columns}} FROM analysis.mov_ave_rsi_ranks WHERE sec_type = {{sec_type}}