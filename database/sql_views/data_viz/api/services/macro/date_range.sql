-- Global min/max dates of stats.v_macro_daily (not just the filtered range)
-- so the frontend can show the complete date range. Serves GET /api/macro.
-- args: (none)
SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM stats.v_macro_daily
