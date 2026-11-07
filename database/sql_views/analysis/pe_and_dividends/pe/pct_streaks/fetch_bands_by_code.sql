-- pe_and_dividends pe_pct_streaks step, --code mode — the pe_pct
-- band rows of ONE code (single-code wholesale rebuild scope).
-- args:
--   sec_type: value smallint — the sec_type label code
--   code: value text — the code being rebuilt
SELECT sec_type, code, date_year_month, period, pct_type, high_val, low_val FROM analysis.pe_pct WHERE sec_type = {{sec_type}} AND code = {{code}} ORDER BY code, date_year_month