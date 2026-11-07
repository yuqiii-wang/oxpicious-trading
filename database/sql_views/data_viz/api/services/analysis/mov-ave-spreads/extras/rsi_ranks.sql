-- MA-Spread "rsiranks" extras group — one code's per-date RSI history
-- ranks (analysis.mov_ave_rsi_ranks — rank 1 = the code's highest RSI
-- reading within its own history, dense 1..N, NULL where that window's
-- RSI is NULL). The client derives each window's top-% cutoff from the
-- max rank over the rows (dense ranks ⇒ max = the count of ranked days),
-- so no totals row is fetched.
-- Serves GET /api/analysis/mov-ave-spread/extras (metrics=rsiranks).
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   codes: value text[] — the code variants to chart (codeVariants)
    SELECT
      r.date,
      r.rank_rsi_3days,
      r.rank_rsi_6days,
      r.rank_rsi_10days,
      r.rank_rsi_14days,
      r.rank_rsi_20days,
      r.rank_rsi_60days
    FROM analysis.mov_ave_rsi_ranks r
    WHERE r.sec_type = {{sec_type}}
      AND r.code = ANY({{codes}}::text[])
    ORDER BY r.date
