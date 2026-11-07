-- Retention prune cutoff (live mode, once per new trading day): the
-- newest RETENTION_DATES distinct 5-min intraday dates across BOTH
-- underlying spot tables (trading-date semantics); the oldest of them is
-- the prune cutoff. Callers do nothing when fewer dates exist than the
-- window (nothing can be outside it).
-- args:
--   keep_dates: value int — the retention window (RETENTION_DATES)

        SELECT DISTINCT date FROM (
            SELECT date FROM stats.etf_intraday_5min
            UNION
            SELECT date FROM stats.index_intraday_5min
        ) d
        ORDER BY date DESC
        LIMIT {{keep_dates}}
