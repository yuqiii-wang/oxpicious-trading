-- options vol-index pipeline — valid raw-settlement rows
-- (strike/S/Q > 0, days_to_expiry > 0) for the CBOE-style
-- model-free 30d vol index; underlying_target_type carries the
-- venue unit convention.
-- args:
--   sec_filter: fragment — the venue filter (empty, or
--       "AND t.underlying_target_type = '<TYPE>'" from
--       _sec_type_where)

        SELECT
            extract(epoch from t.date)::float8 AS date,
            t.contract_code,
            t.option_type,
            t.underlying_code,
            t.underlying_target_type,
            extract(epoch from t.expiry_date)::float8 AS expiry_date,
            t.days_to_expiry,
            k.strike_price::float8 AS strike_price,
            s.underlying_close::float8 AS underlying_close,
            s.settle::float8 AS settle
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        JOIN stats.options_volume_oi v
          ON v.date = t.date AND v.contract_code = t.contract_code
        WHERE 
    k.strike_price > 0
    AND s.underlying_close > 0
    AND s.settle > 0
    AND t.days_to_expiry > 0

          {{sec_filter}}
        ORDER BY t.underlying_code, t.date, t.expiry_date,
                 k.strike_price, t.option_type
    