-- Per (date × real expiry) smile anchors for the skew chronology panel
-- (Spot vs Level vs Skew): the nearest-|x| selections the panel formerly
-- recomputed in-browser from the full /combined blob — ATM by
-- |moneyness_ratio − 1| (NULLS LAST), 25Δ/10Δ OTM wings by |delta| within
-- (0, 0.5)/(−0.5, 0), tie-broken by contract_code (the blob's iteration
-- order, so anchor picks are identical to the old client compute). Row
-- validity mirrors the panel's old filter. option_type codes are
-- static.label_dim SMALLINTs (0 CALL / 1 PUT). Serves
-- GET /api/options/skew-daily.
-- args:
--   where_clause: fragment — the scope WHERE conditions joined " AND "
--     (termsWhere result + the fixed greeks/settlement/strike validity
--     conditions)
--   option_type_call: fragment — LABEL_CODE.option_type.CALL
--   option_type_put: fragment — LABEL_CODE.option_type.PUT
      SELECT t.date, t.expiry_date,
             MIN(t.days_to_expiry) AS dte,
             MAX(st.underlying_close) AS spot,
             (ARRAY_AGG(g.implied_vol * 100.0 ORDER BY abs(st.moneyness_ratio - 1.0) NULLS LAST, t.contract_code))[1] AS atm_iv,
             (ARRAY_AGG(g.implied_vol * 100.0 ORDER BY abs(g.delta - 0.25), t.contract_code)
               FILTER (WHERE t.option_type = {{option_type_call}} AND g.delta > 0 AND g.delta < 0.5))[1] AS call25_iv,
             (ARRAY_AGG(g.implied_vol * 100.0 ORDER BY abs(g.delta + 0.25), t.contract_code)
               FILTER (WHERE t.option_type = {{option_type_put}} AND g.delta < 0 AND g.delta > -0.5))[1] AS put25_iv,
             (ARRAY_AGG(g.implied_vol * 100.0 ORDER BY abs(g.delta - 0.10), t.contract_code)
               FILTER (WHERE t.option_type = {{option_type_call}} AND g.delta > 0 AND g.delta < 0.5))[1] AS call10_iv,
             (ARRAY_AGG(g.implied_vol * 100.0 ORDER BY abs(g.delta + 0.10), t.contract_code)
               FILTER (WHERE t.option_type = {{option_type_put}} AND g.delta < 0 AND g.delta > -0.5))[1] AS put10_iv
      FROM stats.options_terms t
      JOIN stats.options_greeks g ON g.contract_code = t.contract_code AND g.date = t.date
      JOIN stats.options_settlement st ON st.contract_code = t.contract_code AND st.date = t.date
      JOIN stats.options_strike s ON s.contract_code = t.contract_code AND s.date = t.date
      WHERE {{where_clause}}
      GROUP BY t.date, t.expiry_date
      ORDER BY t.date ASC, t.expiry_date ASC
