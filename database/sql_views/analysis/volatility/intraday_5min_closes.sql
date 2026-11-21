-- Volatility pipeline (analysis.volatility) — the day's 5-minute bar
-- closes per (code, date) from the sec_type's own intraday table
-- (stats.{etf,index,stock}_intraday_5min — passed as a fragment arg;
-- industry has no intraday table and skips the rv leg), ordered within
-- the session (bar-to-bar log returns of these closes feed the
-- rv_5min / jump_ratio_5min leg; the overnight gap is excluded by
-- construction — returns are taken WITHIN each (code, date) group).
-- The intraday tables are pruned to a ~90-day retention window, so
-- dates outside it simply return no rows (rv leg stays NULL there).
-- args: intraday_table: fragment —
--         SEC_TYPE_SOURCES[sec_type].intraday_table (None -> no call)
--       codes: value text[] — the run's universe
    SELECT i5.code,
           extract(epoch from i5.date)::float8 AS date_epoch,
           extract(epoch from i5.time)::float8 AS time_epoch,
           i5.close::float8 AS close
    FROM {{intraday_table}} i5
    WHERE i5.code = ANY({{codes}}::text[])
    ORDER BY i5.code, i5.date, i5.time
