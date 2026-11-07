-- mov_ave_spread streaks step — the mov_ave_high_low_pct band rows for
-- ONE sec_type (bands computed by the high-low-percentile step earlier
-- in the same run). Feeds
-- analysis.mov_ave_spread.steps.high_low_pct_streaks.fetch_bands_async.
-- args:
--   bands_table: fragment — the bands table name (HIGH_LOW_PCT_TABLE)
--   sec_type: value smallint — the sec_type's static.label_dim code
SELECT sec_type, code, date_year_month, period, pct_type, high_val, low_val FROM {{bands_table}} WHERE sec_type = {{sec_type}}
