-- MA-Spread "streaks" extras group — the high/low band-BREAK excursion
-- streaks of one (sec_type, code), straight from
-- analysis.mov_ave_high_low_pct_streaks. The streak SIDE (high leg vs low
-- leg) is not stored — derived by joining the band row of the streak's END
-- month and comparing the streak's end close against that band. Fetched
-- once for ALL (period, pct_type) combos; the client filters by its
-- nested period→pct selection.
-- Serves GET /api/analysis/mov-ave-spread/extras (metrics=streaks).
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   codes: value text[] — the code variants to chart (codeVariants)
    SELECT
      s.period,
      s.pct_type,
      s.start_date,
      s.end_date,
      s.open,
      s.close,
      s.high,
      s.low,
      b.high_val AS band_high,
      b.low_val AS band_low,
      s.day_count,
      s.std_dev,
      s.daily_ave_trading_amt,
      -- KEPT CASE (deliberate): a numeric band comparison deriving a
      -- display label — value decoding comes from static.label_dim, but
      -- this derives a label from two bounds, so there is no smallint
      -- code to decode; replacing it would mean a new label_dim domain
      -- (a vocabulary contract change) for no indexability gain.
      CASE
        WHEN s.close > b.high_val THEN 'high'
        WHEN s.close < b.low_val THEN 'low'
      END AS side
    FROM analysis.mov_ave_high_low_pct_streaks s
    JOIN analysis.mov_ave_high_low_pct b
      ON b.sec_type = s.sec_type
      AND b.code = s.code
      AND b.date_year_month = date_trunc('month', s.end_date)::date
      AND b.period = s.period
      AND b.pct_type = s.pct_type
    WHERE s.sec_type = {{sec_type}}
      AND s.code = ANY({{codes}}::text[])
    ORDER BY s.period, s.pct_type, s.start_date
