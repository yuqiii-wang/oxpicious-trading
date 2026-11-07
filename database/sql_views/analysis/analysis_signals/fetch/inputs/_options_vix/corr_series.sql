-- The options_vix family's PUBLISHED corr_20d samples (analysis.
-- options_vol_index_vs_spot_price_corr) over the requested option
-- underlyings — the stride-grid fear-gauge series the engine linearly
-- interpolates to daily in-frame (never interpolated in the DB).
-- args:
--   codes: value text[] — the sorted requested underlying codes
        SELECT underlying_code AS code,
               extract(epoch from date)::float8 AS date,
               corr_20d::float8 AS corr_20d_pub
        FROM analysis.options_vol_index_vs_spot_price_corr
        WHERE underlying_code = ANY({{codes}}::text[])
        ORDER BY underlying_code, date ASC
