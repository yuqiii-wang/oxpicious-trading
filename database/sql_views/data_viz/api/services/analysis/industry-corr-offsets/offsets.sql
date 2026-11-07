-- Industry Correlations by Benchmark Offset — opposite-industry
-- correlation rows audited in
-- analysis_composites.industry_corr_benchmark_offsets, one row per
-- (start_date, lexicographic pair, benchmark) for every pair where both
-- endpoints are in the user-selected industry_ids set. The `cal` CTE
-- maps each window's start date to its trading-day index so the 20/60/255
-- window END dates can be joined back (idx + 19/59/254).
-- The WHERE tail is assembled in the service and passed as a fragment:
-- the pair IN-list carries a VARIABLE number of $n placeholders (2 per
-- pair, $1..$2N) and benchmark_code/pool_size take the LAST slots
-- ($2N+1, $2N+2) — a fixed template cannot reproduce that dynamic
-- numbering, so the fragment carries the placeholders verbatim and the
-- service binds the params array itself.
-- Serves GET /api/analysis/industry-corr-offsets.
-- args:
--   filters: fragment — the WHERE tail built in getIndustryCorrOffsets():
--     `t.benchmark_code = $<2N+1>::text
--      AND t.pool_size = $<2N+2>::smallint
--      AND (t.industry_id, t.benchmark_industry_id) IN (($1::text, $2::text), …)`
--     (pool_size crosses as its static.label_dim SMALLINT code)
    WITH cal AS (
      SELECT date, ROW_NUMBER() OVER (ORDER BY date) - 1 AS idx
      FROM (SELECT DISTINCT date FROM stats.industry_basic_stats) s
    )
    SELECT
      t.industry_id,
      t.benchmark_industry_id,
      t.start_date,
      e20.date AS end_ma20_20d,
      e60.date AS end_ma60_60d,
      e255.date AS end_ma255_255d,
      t."interval",
      t.overall_corr_ma20_20d,
      t.overall_corr_ma60_60d,
      t.overall_corr_ma255_255d,
      t.offset_sub_corr_ma20_20d,
      t.offset_sub_corr_ma60_60d,
      t.offset_sub_corr_ma255_255d
    FROM analysis_composites.industry_corr_benchmark_offsets t
    JOIN cal c ON c.date = t.start_date
    LEFT JOIN cal e20 ON e20.idx = c.idx + 19
    LEFT JOIN cal e60 ON e60.idx = c.idx + 59
    LEFT JOIN cal e255 ON e255.idx = c.idx + 254
    WHERE {{filters}}
    ORDER BY t.start_date ASC, t.industry_id, t.benchmark_industry_id
