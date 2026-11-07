-- Hypes & Drains (SEASONAL) — distinct season keys with their date
-- boundaries for ONE (benchmark_code, period_days, weighting).
-- Serves GET /api/analysis/industry-hypes-and-drains.
-- args:
--   benchmark_code: value text — the benchmark index code
--   period_days: value int — the ranking window (5/20/60/120/255/500)
--   weighting_code: value smallint — weighting's static.label_dim code
    SELECT DISTINCT season_qkey, season_start, season_end
    FROM analysis.industry_hypes_seasonal
    WHERE benchmark_code = {{benchmark_code}}::text
      AND period_days = {{period_days}}::int
      AND weighting = {{weighting_code}}::smallint
    ORDER BY season_qkey
