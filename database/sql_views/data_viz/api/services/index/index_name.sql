-- Display name of ONE index (mode over identity rows). Shared by
-- /api/index/daily and /api/index/intraday-5min.
-- args:
--   code: value text — the index code
SELECT code, MAX(name) AS name FROM stats.index_identity WHERE code = {{code}} GROUP BY code
