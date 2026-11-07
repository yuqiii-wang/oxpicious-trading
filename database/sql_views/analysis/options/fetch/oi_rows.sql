-- options OI pipeline — all valid contract rows (strike > 0,
-- underlying_close > 0) for the per-expiry OI stats compute.
-- args:
--   sec_filter: fragment — the venue filter (empty, or
--       "AND t.underlying_target_type = '<TYPE>'" from
--       _sec_type_where)

        SELECT
            extract(epoch from t.date)::float8 AS date,
            t.contract_code,
            t.option_type,
            t.underlying_code,
            extract(epoch from t.expiry_date)::float8 AS expiry_date,
            v.open_interest::float8 AS open_interest,
            s.underlying_close::float8 AS underlying_close
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

          {{sec_filter}}
        ORDER BY t.underlying_code, t.expiry_date, t.date
    