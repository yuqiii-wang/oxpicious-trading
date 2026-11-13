-- Stock attribution step (analysis.market_beta internal pipeline) —
-- self-managing incremental target-date detection (the cross_stats
-- calendar the sibling steps follow does NOT cover the stock daily
-- build's dates): DISTINCT source dates (stock_basic_stats joinable to
-- the stock->industry mapping) missing from
-- analysis.industry_stock_change_dist. Reads filter on the target
-- table's primary key grain (date) per the incremental convention.
-- args: none
WITH src AS (
    SELECT DISTINCT s.date
    FROM stats.stock_basic_stats s
    JOIN (
        SELECT DISTINCT ON (code) code
        FROM stats.sec_classification
        WHERE type = 'stock'
        ORDER BY code, parent_index_is_primary DESC,
                 parent_index_weight DESC NULLS LAST
    ) sm ON sm.code = s.code
    WHERE s.pct_change IS NOT NULL
),
got AS (
    SELECT DISTINCT date FROM analysis.industry_stock_change_dist
)
SELECT s.date
FROM src s LEFT JOIN got g ON g.date = s.date
WHERE g.date IS NULL
ORDER BY s.date
