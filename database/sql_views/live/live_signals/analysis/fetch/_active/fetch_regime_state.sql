-- The code's market-regime DAY label code ON one date (stats.market_regimes
-- point lookup, partition-pruned by the code-leading PK; the daily labels
-- are static historical rows, as-of-safe by construction). 0 (calm) when
-- the day has no state row — the engine's own regime-join default.
-- args:
--   sec_type: value smallint — the sec_type's SEC_TYPE_CODE
--   code: value text — the ticker
--   on_date: value date — the day to look up

        SELECT regime FROM stats.market_regimes r
        WHERE r.sec_type = {{sec_type}} AND r.code = {{code}} AND r.date = {{on_date}}
        LIMIT 1
