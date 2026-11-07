-- Options combined rows — all quote columns for the requested (venue,
-- underlying, date-range) scope, driven from the FILTERED options_terms
-- side instead of the v_options_quote view: underlying_code only exists on
-- terms, so through the view the planner cannot push the filter into any
-- sub-table (it scanned ~832k CFFEX rows out of EVERY options_* table,
-- 15s+ per request). Driving from terms gives a ~400k-row outer side it
-- can hash-join against each sub-table. Runs under work_mem=256MB + no
-- parallel workers per gather (see COMBINED_SESSION_SETS in
-- options.service.ts). Serves GET /api/options/combined.
-- args:
--   where_clause: fragment — conditional scope WHERE
--     ("WHERE t.underlying_target_type = $1 AND ..." etc.) or ""
    SELECT
        t.date, i.contract_code, i.contract_name, i.exchange,
        t.underlying_code, t.underlying_name, t.option_type,
        t.expiry_month, t.expiry_date, t.days_to_expiry,
        s.strike_price, st.settle, st.underlying_close, st.moneyness_ratio,
        vo.open_interest, vo.volume,
        g.implied_vol, g.delta, g.theta, g.gamma, g.vega, g.rho
    FROM stats.options_terms t
    JOIN stats.options_identity i
        ON i.contract_code = t.contract_code AND i.date = t.date
    LEFT JOIN stats.options_strike s
        ON s.contract_code = t.contract_code AND s.date = t.date
    LEFT JOIN stats.options_settlement st
        ON st.contract_code = t.contract_code AND st.date = t.date
    LEFT JOIN stats.options_greeks g
        ON g.contract_code = t.contract_code AND g.date = t.date
    LEFT JOIN stats.options_volume_oi vo
        ON vo.contract_code = t.contract_code AND vo.date = t.date
 {{where_clause}} ORDER BY t.date ASC, t.contract_code ASC
