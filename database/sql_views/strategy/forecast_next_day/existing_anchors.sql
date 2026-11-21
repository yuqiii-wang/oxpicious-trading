-- The run's codes' LATEST registered forecast anchors — the pipeline's
-- INCREMENTAL input: a code whose anchor already matches its OHLCV
-- horizon (max close date) is skipped (its scenario rows are current);
-- --force purges the scope and regenerates instead. Reads filter on
-- the table's primary key columns (anchor_date, sec_type, code, ...).
-- args:
--   sec_type: value smallint — the sec_type's SEC_TYPE_CODE
--   codes: value text[] — the run's codes
SELECT code, MAX(anchor_date) AS anchor_date
FROM strategy.forecast_next_day
WHERE sec_type = {{sec_type}} AND code = ANY({{codes}})
GROUP BY code
