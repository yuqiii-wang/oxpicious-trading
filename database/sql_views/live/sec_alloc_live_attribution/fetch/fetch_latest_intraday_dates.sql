-- The latest N distinct intraday 5-min dates in stats.index_intraday_5min
-- (the pipeline's live-date resolution and the retention prune's keep
-- window; heavy refs (re)build for the LATEST date only — on
-- weekends/holidays that is simply the last trading day).
-- args:
--   n_dates: value int — how many newest distinct dates to return
SELECT DISTINCT date
FROM stats.index_intraday_5min
WHERE close IS NOT NULL
ORDER BY date DESC
LIMIT {{n_dates}}::int
