-- Options OI Stats — per-expiry OI level/changes from
-- analysis.options_oi_stats, aggregated per month group. The table stores
-- per-REAL-expiry rows duplicated per option_type (CALL/PUT hold the same
-- value): the inner DISTINCT dedupes the option_type duplication; the
-- outer GROUP BY aggregates the month group the frontend keys on — SUM
-- across the month's expiries (1 per month for the monthly ETF convention —
-- exact; CFFEX weeklies sum to the month group's total), MAX of the
-- per-expiry trailing maxes (approximate for multi-expiry months). Serves
-- GET /api/options/oi-stats.
-- args:
--   where_clause: fragment — the scope WHERE conditions joined " AND "
--     (starts with the fixed "underlying_code = $1"), applied INSIDE the
--     DISTINCT subquery
    SELECT
      date,
      underlying_code,
      expiry_month,
      MAX(expiry_date) AS expiry_date,
      SUM(oi_total) AS oi_total,
      SUM(oi_delta_5d) AS oi_delta_5d,
      SUM(oi_delta_20d) AS oi_delta_20d,
      MAX(oi_max_20d) AS oi_max_20d
    FROM (
      SELECT DISTINCT date, underlying_code,
             DATE_TRUNC('month', expiry_date) AS expiry_month,
             expiry_date,
             oi_total, oi_delta_5d, oi_delta_20d, oi_max_20d
      FROM analysis.options_oi_stats
      WHERE {{where_clause}}
    ) t
    GROUP BY date, underlying_code, expiry_month
    ORDER BY date ASC, expiry_month ASC
