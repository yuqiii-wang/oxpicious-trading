-- Hypes & Drains (SEASONAL) — all monthly top/bottom rankings for ONE
-- (benchmark_code, period_days, weighting) from
-- analysis.industry_hypes_seasonal. rank_side decodes to its TEXT label
-- via static.label_dim (the response stays strings); weighting filters
-- by its label-dim SMALLINT code. Drives the ACTIVE/FADING/HIDDEN state
-- machine on the "Hypes & Drains" view.
-- Serves GET /api/analysis/industry-hypes-and-drains.
-- args:
--   benchmark_code: value text — the benchmark index code
--   period_days: value int — the ranking window (5/20/60/120/255/500)
--   weighting_code: value smallint — weighting's static.label_dim code
--   rank_side_decode: fragment — labelDecodeSql("rank_side", "rank_side")
    SELECT season_qkey,
           {{rank_side_decode}} AS rank_side,
           rank, industry_id, industry_label,
           peak_metric_value
    FROM analysis.industry_hypes_seasonal
    WHERE benchmark_code = {{benchmark_code}}::text
      AND period_days = {{period_days}}::int
      AND weighting = {{weighting_code}}::smallint
    ORDER BY season_qkey, rank_side, rank
