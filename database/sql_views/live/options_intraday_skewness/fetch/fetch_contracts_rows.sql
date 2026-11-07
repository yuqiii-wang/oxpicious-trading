-- Prev-trading-day contract snapshot rows at the resolved snapshot date —
-- the estimator's OI base AND the terms the skew groups by (option_type,
-- expiry_date, strike in 厘, implied_vol for the validity filter).
-- args:
--   snapshot_date: value date — the resolved v_options_quote snapshot date
        SELECT contract_code, underlying_code, option_type, expiry_date,
               strike_price, implied_vol, open_interest
        FROM stats.v_options_quote
        WHERE date = {{snapshot_date}} AND strike_price > 0
