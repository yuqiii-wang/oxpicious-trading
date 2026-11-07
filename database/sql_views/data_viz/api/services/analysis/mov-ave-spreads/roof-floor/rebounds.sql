-- MA-Spread "rfaudit" extras group — one code's rebound-audit events
-- (analysis.mov_ave_spreads_roof_floor_rebounds). side + outcome decode to
-- their label_dim TEXT labels at this boundary (labels never ride frames);
-- NUMERICs cast float8, periods cast int server-side.
-- Serves GET /api/analysis/mov-ave-spread/extras (metrics=rfaudit).
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   codes: value text[] — the code variants to chart (codeVariants)
--   side_decode: fragment — labelDecodeSql("side", "r.side")
--   outcome_decode: fragment — labelDecodeSql("rf_rebound_outcome", "r.outcome")
    SELECT
      r.event_date,
      {{side_decode}} AS side,
      r.period::int AS period,
      r.level::float8,
      {{outcome_decode}} AS outcome,
      r.rebound_date,
      r.retest_date,
      r.days_to_retest::int AS days_to_retest,
      r.rebound_pct::float8,
      r.bounce_pct::float8,
      r.break_depth_pct::float8,
      r.days_to_break::int AS days_to_break,
      r.clean_break,
      r.later_break,
      r.retest_episodes::int AS retest_episodes
    FROM analysis.mov_ave_spreads_roof_floor_rebounds r
    WHERE r.sec_type = {{sec_type}}
      AND r.code = ANY({{codes}}::text[])
    ORDER BY r.event_date
