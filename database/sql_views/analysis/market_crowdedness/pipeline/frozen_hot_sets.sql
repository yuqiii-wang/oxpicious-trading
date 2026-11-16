-- Market crowdedness — the FROZEN closed-year hot-set rosters
-- (analysis.market_crowdedness_hot_sets) for the scoped sec_types:
-- incremental runs read them back instead of re-deriving (their
-- year data lies outside the load window).
-- args:
--   sec_types: value smallint[] — the scoped static.label_dim sec_type codes
    SELECT sec_type,
           code AS pool_code,
           hot_year,
           hot_rank,
           stock_code
    FROM analysis.market_crowdedness_hot_sets
    WHERE sec_type = ANY({{sec_types}}::smallint[])
    ORDER BY pool_code, hot_year, hot_rank ASC
