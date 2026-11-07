-- Display name of the index itself — the unified ETF→feeder table's Index
-- column shows "index_code index_name". Serves
-- GET /api/sec-composition/linked-etfs.
-- args:
--   index_code: value text — the index code
SELECT name FROM stats.sec_classification WHERE code = {{index_code}} AND type = 'index' LIMIT 1
