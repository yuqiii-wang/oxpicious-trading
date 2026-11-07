-- options moneyness pipeline — ALL in-scope contract rows on a
-- LEFT-join basis (NULL-tolerant; valid filters applied in
-- compute) — the materialized oi_moneyness series source.
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
            g.implied_vol::float8 AS implied_vol
        FROM stats.options_terms t
        LEFT JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        LEFT JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        LEFT JOIN stats.options_volume_oi v
          ON v.date = t.date AND v.contract_code = t.contract_code
        LEFT JOIN stats.options_greeks g
          ON g.date = t.date AND g.contract_code = t.contract_code
        WHERE 1=1
          {{sec_filter}}
        ORDER BY t.underlying_code, t.date, t.expiry_date
    