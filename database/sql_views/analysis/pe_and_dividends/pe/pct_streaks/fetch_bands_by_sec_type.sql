-- pe_and_dividends pe_pct_streaks step — the pe_pct band rows for
-- one sec_type (bands written by the pct_bands step earlier in
-- the same run; the streak compute audits days against them).
-- args:
--   sec_type: value smallint — the sec_type label code
SELECT sec_type, code, date_year_month, period, pct_type, high_val, low_val FROM analysis.pe_pct WHERE sec_type = {{sec_type}} ORDER BY code, date_year_month