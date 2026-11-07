-- Per (date × expiry × strike) call/put OI cells for the bands chart,
-- restricted to the requested expiry dates (the panel's cohort pick).
-- option_type codes are static.label_dim SMALLINTs (0 CALL / 1 PUT).
-- Serves GET /api/options/oi-cells.
-- args:
--   where_clause: fragment — the scope WHERE conditions joined " AND "
--     (termsWhere result + "t.expiry_date = ANY($n::date[])")
--   option_type_call: fragment — LABEL_CODE.option_type.CALL
--   option_type_put: fragment — LABEL_CODE.option_type.PUT
    SELECT t.date, t.expiry_date, s.strike_price,
           COALESCE(SUM(vo.open_interest) FILTER (WHERE t.option_type = {{option_type_call}}), 0) AS call_oi,
           COALESCE(SUM(vo.open_interest) FILTER (WHERE t.option_type = {{option_type_put}}), 0)  AS put_oi
    FROM stats.options_terms t
    JOIN stats.options_strike s ON s.contract_code = t.contract_code AND s.date = t.date
    JOIN stats.options_volume_oi vo ON vo.contract_code = t.contract_code AND vo.date = t.date
    WHERE {{where_clause}}
    GROUP BY t.date, t.expiry_date, s.strike_price
    ORDER BY t.date ASC, t.expiry_date ASC, s.strike_price ASC
