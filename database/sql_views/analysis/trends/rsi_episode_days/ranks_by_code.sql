-- trends rsi_episode_days, --code mode — the RSI rank columns
-- for one (sec_type, code) from analysis.mov_ave_rsi_ranks.
-- args:
--   rank_columns: fragment — the rank_rsi_{W}days select list
--       (", ".join over RSI_WINDOWS)
--   sec_type: value smallint — the sec_type label code
--   code: value text — the code being rebuilt
SELECT sec_type, code, date, {{rank_columns}} FROM analysis.mov_ave_rsi_ranks WHERE sec_type = {{sec_type}} AND code = {{code}}