-- Industry Correlations — windowed pairwise Pearson correlations between
-- industries' MA curves of mean_close, one row per (start_date,
-- industry_id, benchmark_industry_id, pool_size, interval) for every
-- stored pair where both endpoints are in the user-selected industry_ids
-- set (either direction of the stored row).
-- The WHERE tail is assembled in the service and passed as a fragment:
-- the pair IN-list carries a VARIABLE number of $n placeholders (2 per
-- pair, $1..$2N) and pool_size takes the LAST slot ($2N+1) — a fixed
-- template cannot reproduce that dynamic numbering, so the fragment
-- carries the placeholders verbatim and the service binds the params
-- array itself. Serves GET /api/analysis/industry-correlations.
-- args:
--   filters: fragment — the WHERE tail built in getIndustryCorrelations():
--     `pool_size = $<2N+1>::smallint
--      AND (industry_id, benchmark_industry_id) IN (($1::text, $2::text), …)`
--     (pool_size crosses as its static.label_dim SMALLINT code)
    SELECT
      industry_id,
      benchmark_industry_id,
      start_date,
      "interval",
      corr_ma20_20d,
      corr_ma60_60d,
      corr_ma255_255d
    FROM analysis.industry_correlations
    WHERE {{filters}}
    ORDER BY start_date ASC, industry_id, benchmark_industry_id
