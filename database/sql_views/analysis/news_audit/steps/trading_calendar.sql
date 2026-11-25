-- The SSE Composite trading calendar — the day grid the coverage audit
-- rolls article calendar dates onto (weekend/holiday articles roll
-- forward to the next trading day; analysis._common.calendar_roll).
-- Fixed warmup bound 2019-06-01 (index history starts 2020-01-01).
-- args:
--   index_code: value text — the calendar's index ('000001')
SELECT DISTINCT EXTRACT(EPOCH FROM b.date)::float8 AS date_epoch
FROM stats.v_index_daily b
WHERE b.code = {{index_code}}::text
  AND b.date >= DATE '2019-06-01'
ORDER BY date_epoch
