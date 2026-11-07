-- Dividend Deliveries — one code's rolling 5-year (1-year stride) dividend
-- delivery-audit windows from analysis.dividend_deliveries, newest first
-- (per-event yield, eps and the stability flag aggregated over each
-- window; built by analysis.pe_and_dividends --metrics deliveries). Codes
-- with no rows (non-payers / index) resolve to an empty list.
-- Serves GET /api/analysis/dividends/deliveries
-- args:
--   sec_type: value smallint — LABEL_CODE.sec_type[st]
--   codes: value text[] — codeVariants(code) (bare ETF codes AND suffixed
--     stock codes, whichever form the caller sends)
    SELECT window_end_date, window_start_date, deliveries, years_with_delivery,
           avg_yield, annualized_yield, latest_year_yield, avg_eps,
           stable_events, stability
      FROM analysis.dividend_deliveries
     WHERE sec_type = {{sec_type}} AND code = ANY({{codes}}::text[])
     ORDER BY window_end_date DESC
