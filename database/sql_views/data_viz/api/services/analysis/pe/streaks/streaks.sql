-- PE streaks — the band-BREAK excursion streaks of one code's pe series,
-- from analysis.pe_pct_streaks joined with its end-month band row in
-- analysis.pe_pct. The streak SIDE is not stored — it is derived by
-- comparing the streak's end_value against the band of the streak's END
-- month (each streak day is tested against its OWN month's band, and a
-- streak never switches sides, so the end day's own-month band decides
-- exactly). Shipped flat for ALL (period, pct_type) combos — the client
-- filters by its nested period→pct selection.
-- Serves GET /api/analysis/pe/streaks.
-- args:
--   sec_type: value smallint — static.label_dim 'sec_type' code (LABEL_CODE.sec_type)
--   codes: value text[] — the code's variants (codeVariants)
    SELECT
      'pe' AS metric,
      s.period,
      s.pct_type,
      s.start_date,
      s.end_date,
      s.start_value,
      s.end_value,
      s.max_value,
      s.min_value,
      b.high_val AS band_high,
      b.low_val  AS band_low,
      s.day_count,
      s.std_dev,
      -- KEPT CASE (deliberate): a numeric band comparison deriving a
      -- display label — value decoding comes from static.label_dim, but
      -- this derives a label from two bounds, so there is no smallint
      -- code to decode; replacing it would mean a new label_dim domain
      -- (a vocabulary contract change) for no indexability gain.
      CASE
        WHEN s.end_value > b.high_val THEN 'high'
        WHEN s.end_value < b.low_val  THEN 'low'
      END AS side
    FROM analysis.pe_pct_streaks s
    JOIN analysis.pe_pct b
      ON b.sec_type = s.sec_type
      AND b.code = s.code
      AND b.date_year_month = date_trunc('month', s.end_date)::date
      AND b.period = s.period
      AND b.pct_type = s.pct_type
    WHERE s.sec_type = {{sec_type}}
      AND s.code = ANY({{codes}}::text[])
    ORDER BY period, pct_type, start_date
