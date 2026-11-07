-- ETF contribution step (analysis.market_beta internal pipeline) — the
-- full server-side aggregation of stats.index_exts ETF turnover per
-- (date, industry_id, pool_size). CTE chain:
--   etf_amt      — (code, date, total_etf_trading_amount) source rows;
--                  the date_filter fragment applies HERE ONLY (incremental
--                  mode targets specific dates).
--   index_info   — per-index industry_id / industry_label / stock_num
--                  (COUNT(DISTINCT stock_code) in sec_composition, via
--                  LEFT JOIN LATERAL — same as builds.industry).
--   pool_rows    — static.label_dim pool codes: 3 (all) for every index
--                  plus the specific pool (0 small / 1 mid / 2 large) by
--                  stock_num; UNION ALL expands each index into 1-2 rows.
--   Final SELECT — JOIN etf_amt with pool_rows, GROUP BY (date,
--                  industry_id, pool_size): industry_etf_count =
--                  COUNT(DISTINCT code), industry_etf_trading_amount =
--                  SUM. MA5 / MA20 are computed in pandas afterwards.
-- args:
--   date_filter: fragment — "" for the full recompute; incremental passes
--     "AND ie.date = ANY($1::date[])" and binds the sorted target-date
--     array as the statement's $1 driver param
WITH etf_amt AS (
    SELECT
        ie.code,
        ie.date,
        ie.total_etf_trading_amount AS code_etf_trading_amount
    FROM stats.index_exts ie
    WHERE ie.total_etf_trading_amount IS NOT NULL
      {{date_filter}}
),
index_info AS (
    SELECT
        cls.code,
        cls.industry_id,
        COALESCE(cls.industry_label, cls.industry_id) AS industry_label,
        sc.stock_num
    FROM (
        SELECT DISTINCT code, industry_id, industry_label
        FROM stats.sec_classification
        WHERE type = 'index'
          AND is_active = TRUE
          AND is_industry_not_strategy = TRUE
          AND industry_id IS NOT NULL
          AND industry_id <> ''
    ) cls
    LEFT JOIN LATERAL (
        SELECT COUNT(DISTINCT stock_code) AS stock_num
        FROM stats.sec_composition
        WHERE source_type = 'index'
          AND code = cls.code
    ) sc ON true
),
-- Generate pool_size rows (static.label_dim SMALLINT codes: 0 small /
-- 1 mid / 2 large / 3 all): 3 (all) for every index, plus the specific
-- pool based on stock_num.
pool_rows AS (
    SELECT code, industry_id, industry_label, 3::smallint AS pool_size
    FROM index_info
    UNION ALL
    SELECT code, industry_id, industry_label, 0::smallint AS pool_size
    FROM index_info WHERE stock_num IS NOT NULL AND stock_num < 51
    UNION ALL
    SELECT code, industry_id, industry_label, 1::smallint AS pool_size
    FROM index_info WHERE stock_num IS NOT NULL
                       AND stock_num >= 51 AND stock_num <= 180
    UNION ALL
    SELECT code, industry_id, industry_label, 2::smallint AS pool_size
    FROM index_info WHERE stock_num IS NOT NULL AND stock_num > 180
)
SELECT
    extract(epoch from ea.date)::float8 AS date,
    pr.industry_id,
    pr.industry_label,
    pr.pool_size,
    COUNT(DISTINCT ea.code) AS industry_etf_count,
    SUM(ea.code_etf_trading_amount) AS industry_etf_trading_amount
FROM etf_amt ea
JOIN pool_rows pr ON pr.code = ea.code
GROUP BY ea.date, pr.industry_id, pr.industry_label, pr.pool_size
