-- options walls pipeline — valid contract rows (strike > 0) with
-- OI and the LEFT-joined underlying_close required by the zone
-- wall pipeline (gap / BREACHED state).
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
            v.open_interest::float8 AS open_interest,
            s.underlying_close::float8 AS underlying_close
        FROM stats.options_terms t
        JOIN stats.options_strike k
          ON k.date = t.date AND k.contract_code = t.contract_code
        JOIN stats.options_volume_oi v
          ON v.date = t.date AND v.contract_code = t.contract_code
        LEFT JOIN stats.options_settlement s
          ON s.date = t.date AND s.contract_code = t.contract_code
        WHERE k.strike_price > 0
          {{sec_filter}}
        ORDER BY t.option_type, t.underlying_code, t.expiry_date, t.date
    