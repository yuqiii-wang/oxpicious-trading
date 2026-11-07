-- mov_ave_spread streaks step — the mov_ave_high_low_pct band rows for
-- ONE sec_type scoped to a single code (--code mode: the caller deleted
-- + recomputed the code's bands first). Feeds
-- analysis.mov_ave_spread.steps.high_low_pct_streaks.fetch_bands_async.
-- args:
--   bands_table: fragment — the bands table name (HIGH_LOW_PCT_TABLE)
--   sec_type: value smallint — the sec_type's static.label_dim code
--   code: value text — the single code to scope to
SELECT sec_type, code, date_year_month, period, pct_type, high_val, low_val FROM {{bands_table}} WHERE sec_type = {{sec_type}} AND code = {{code}}
