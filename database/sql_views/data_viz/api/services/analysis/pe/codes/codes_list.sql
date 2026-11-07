-- Valuation codes list — one row per code with first/last date, n_dates
-- and the latest pe + dividend_yield snapshot (for sparkline / sort), read
-- from the PRECOMPUTED per-code summary analysis.pe_codes (maintained by
-- analysis.pe_and_dividends' refresh_pe_codes_summary after every detail
-- write pass; the former live aggregate took 4.7s index / 35s stock per
-- request). Serves the SHARED /api/analysis/valuation/codes route used by
-- BOTH the PE and Dividends pages.
-- args:
--   sec_type: value smallint — static.label_dim 'sec_type' code (LABEL_CODE.sec_type)
    SELECT
      c.code,
      c.name,
      c.first_date,
      c.last_date,
      c.n_dates,
      c.latest_pe,
      c.latest_dividend_yield
    FROM analysis.pe_codes c
    WHERE c.sec_type = {{sec_type}}
    ORDER BY c.code
