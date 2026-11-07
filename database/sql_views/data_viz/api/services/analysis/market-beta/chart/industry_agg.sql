-- Market-beta chart — precomputed mean/var aggregation rows for ONE
-- industry from stats.industry_basic_stats, all 4 pool_size slices (the
-- frontend picks the user-selected one). pool_size decodes to its TEXT
-- label (UI contract); ORDER BY keeps the pre-refactor TEXT presentation
-- order via label_dim.sort_order. Serves GET /api/analysis/market-beta chart.
-- args:
--   industry_id: value text — the industry to aggregate
--   pool_size_decode: fragment — labelDecodeSql("pool_size", "pool_size")
--   pool_size_sort: fragment — POOL_SIZE_SORT_SQL
    SELECT date,
           {{pool_size_decode}} AS pool_size,
           index_count, mean_close, var_price, mean_pe, total_trading_amount
    FROM stats.industry_basic_stats
    WHERE industry_id = {{industry_id}}::text
    ORDER BY {{pool_size_sort}}, date ASC
