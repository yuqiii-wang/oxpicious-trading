-- options IV-skew pipeline — contract rows with valid IV + delta
-- carrying all greeks (implied_vol in (0, 5), delta non-null)
-- for the IV skew / greek skew / bands-IV computes.
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
            k.strike_price::float8 AS strike_price,
            s.underlying_close::float8 AS underlying_close,
            v.open_interest::float8 AS open_interest,
            g.implied_vol::float8 AS implied_vol,
            g.delta::float8 AS delta,
            g.theta::float8 AS theta,
            g.gamma::float8 AS gamma,
            g.vega::float8 AS vega,
            g.rho::float8 AS rho
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        JOIN stats.options_volume_oi v
          ON v.date = t.date AND v.contract_code = t.contract_code
        JOIN stats.options_greeks g
          ON g.date = t.date AND g.contract_code = t.contract_code
        WHERE 
    k.strike_price > 0
    AND s.underlying_close > 0
    AND g.implied_vol > 0
    AND g.implied_vol < 5
    AND g.delta IS NOT NULL

          {{sec_filter}}
        ORDER BY t.underlying_code, t.expiry_date, t.date
    