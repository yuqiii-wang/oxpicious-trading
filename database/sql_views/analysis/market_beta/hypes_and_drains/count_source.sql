-- Hypes & drains step (analysis.market_beta internal pipeline) guard —
-- bail out early if analysis.industry_attributions is empty / missing OR
-- the DAILY non-this-industry price column (the raw material for the
-- daily identity inversion) has not been populated. Filter on
-- attribution_type='trading_amt' to avoid duplicate counting — both
-- weighting variants share the same daily columns.
-- args: none
    SELECT COUNT(*) AS n
    FROM analysis.industry_attributions ia
    JOIN stats.sec_index_tags sit ON sit.code = ia.benchmark_code
    WHERE sit.is_broad_market = TRUE
      AND ia.attribution_type = 'trading_amt'
      AND ia.benchmark_non_this_industry_price IS NOT NULL
