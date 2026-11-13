-- The sec_type's FULL emission universe (--codes 'all', 2026-11-07):
-- distinct codes with at least one clean-close row in the base OHLCV
-- table — the same admission filter the family fetches apply
-- (close IS NOT NULL). The daily signals run resolves its codes from
-- here instead of the 10 study indices (user-directed "only a small
-- num of codes are run daily, now add for all indexes").
-- args:
--   base_table: fragment — the sec_type's base OHLCV table
--     (stats.{index,etf,stock}_basic_stats)
        SELECT DISTINCT code
        FROM {{base_table}}
        WHERE close IS NOT NULL
