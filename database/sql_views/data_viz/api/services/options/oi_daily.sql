-- Per (date × expiry) call/put OI + per-date spot — the trend charts'
-- daily aggregates and shared x-axis/spot line. The spot close rides the
-- same scan (MAX over the date partition of the grouped rows) instead of a
-- second full-history terms ⋈ settlement aggregation per request.
-- option_type codes are static.label_dim SMALLINTs (0 CALL / 1 PUT).
-- Serves GET /api/options/oi-daily.
-- args:
--   where_clause: fragment — conditional scope WHERE (termsWhere result
--     incl. the WHERE keyword) or ""
--   option_type_call: fragment — LABEL_CODE.option_type.CALL
--   option_type_put: fragment — LABEL_CODE.option_type.PUT
      SELECT t.date, t.expiry_date,
             COALESCE(SUM(vo.open_interest) FILTER (WHERE t.option_type = {{option_type_call}}), 0) AS call_oi,
             COALESCE(SUM(vo.open_interest) FILTER (WHERE t.option_type = {{option_type_put}}), 0)  AS put_oi,
             MAX(MAX(st.underlying_close)) OVER (PARTITION BY t.date) AS spot_close
      FROM stats.options_terms t
      JOIN stats.options_volume_oi vo ON vo.contract_code = t.contract_code AND vo.date = t.date
      LEFT JOIN stats.options_settlement st ON st.contract_code = t.contract_code AND st.date = t.date
      {{where_clause}}
      GROUP BY t.date, t.expiry_date
      ORDER BY t.date ASC, t.expiry_date ASC
