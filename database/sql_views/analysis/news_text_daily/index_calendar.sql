-- The SSE Composite trading calendar — the day grid every metric curve
-- reindexes onto (weekend articles roll forward to the next calendar day).
-- Fixed warmup bound 2019-06-01 so the trailing windows (60d z / 63d flow)
-- are defined from the earliest possible target date (index history
-- starts 2020-01-01).
-- args:
--   index_code: value text — the calendar's index (scope kinds 0/1 use '000001')
SELECT DISTINCT EXTRACT(EPOCH FROM b.date)::float8 AS date_epoch
FROM stats.v_index_daily b
WHERE b.code = {{index_code}}::text
  AND b.date >= DATE '2019-06-01'
ORDER BY date_epoch
