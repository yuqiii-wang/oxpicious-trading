-- options pipeline 0 — distinct (date, option_type,
-- underlying_code, expiry_date) tuples populating
-- analysis.options_expiry_identity (open expiry collapses in
-- pandas afterwards).
-- args:
--   sec_filter: fragment — the venue filter (empty, or
--       "AND t.underlying_target_type = '<TYPE>'" from
--       _sec_type_where)

        SELECT DISTINCT extract(epoch from t.date)::float8 AS date,
               t.option_type, t.underlying_code,
               extract(epoch from t.expiry_date)::float8 AS expiry_date
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        WHERE 
    k.strike_price > 0
    AND s.underlying_close > 0

          {{sec_filter}}
        ORDER BY date, option_type, underlying_code, expiry_date
    