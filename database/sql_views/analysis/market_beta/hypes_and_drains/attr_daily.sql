-- Hypes & drains step (analysis.market_beta internal pipeline) —
-- (benchmark, date, industry) daily decomposition legs. attribution_type
-- ='trading_amt' only — BOTH weightings read these identical daily
-- columns; the 'equal' rows would duplicate the same values. The
-- broad-market scope is an IN-subquery, NOT a JOIN: sec_index_tags is
-- keyed (code, sector_id, industry_id) and a benchmark can carry SEVERAL
-- broad-market tag rows (e.g. 000001 = BROAD_SSE + benchmark_broadmarket)
-- — a JOIN would fan every attribution row out per tag row. The
-- shared_return_1d inversion + per-period compounding are computed in
-- pandas afterwards.
-- args: none
    SELECT ia.benchmark_code,
           extract(epoch from ia.date)::float8                 AS date,
           ia.industry_id,
           ia.benchmark_shared_weight::float8                  AS benchmark_shared_weight,
           ia.benchmark_non_this_industry_price::float8        AS non_ind_price,
           ia.benchmark_non_this_industry_trading_amt::float8  AS non_ind_amt
    FROM analysis.industry_attributions ia
    WHERE ia.attribution_type = 'trading_amt'
      AND ia.benchmark_non_this_industry_price IS NOT NULL
      AND ia.benchmark_code IN (
          SELECT sit.code FROM stats.sec_index_tags sit
          WHERE sit.is_broad_market = TRUE)
    ORDER BY ia.benchmark_code, ia.date, ia.industry_id
