-- Daily data check — analysis_daily step 12
-- (analysis.market_participation): analysis.market_participation (one
-- row per trading day, PK (date)) vs the stats.exchange_trading_amt
-- SS-and-SZ frontier — the _missing_dates source the module itself
-- uses (a day counts when BOTH exchanges' proxy rows exist). A gap
-- means the nightly participation pass stopped writing. STRICT
-- (0-day): the build fills from the exchange dates in the same
-- nightly pass, compared at 07:00 the morning after.
-- Serves analysis.market_participation (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- table-wide row when stale).
-- args: none
SELECT *
FROM (
    SELECT NULL::text AS code,
           (SELECT MAX(date) FROM analysis.market_participation)
               AS latest_date,
           (SELECT MAX(ss.date)
            FROM stats.exchange_trading_amt ss
            JOIN stats.exchange_trading_amt sz
              ON sz.date = ss.date AND sz.exchange = 'SZ'
            WHERE ss.exchange = 'SS') AS frontier_date
) t
WHERE t.latest_date IS NULL OR t.latest_date < t.frontier_date
