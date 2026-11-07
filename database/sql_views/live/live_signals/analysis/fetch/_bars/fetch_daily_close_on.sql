-- The code's OFFICIAL daily close on the date from the sec_type's
-- basic_stats baseline (the as-of mode's no-intraday-data fallback; None
-- when the day has no daily row — non-trading day, ingestion gap, or
-- outside retention).
-- args:
--   daily_table: fragment — the sec_type's daily table (DAILY_TABLES[sec_type])
--   code: value text — the ticker
--   on_date: value date — the as-of day
SELECT close::float8 AS close FROM {{daily_table}} WHERE code = {{code}} AND date = {{on_date}} AND close IS NOT NULL   AND close::text <> 'NaN' LIMIT 1
