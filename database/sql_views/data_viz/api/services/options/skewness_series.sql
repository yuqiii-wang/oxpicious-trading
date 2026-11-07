-- Options Skewness Series — daily raw skewness per (date, expiry month)
-- from analysis.options_skewness_stats. Serves
-- GET /api/options/skewness-series.
-- args:
--   where_clause: fragment — the scope WHERE conditions joined " AND "
--     (starts with the fixed underlying_code/skew_type pair)
    SELECT
      date,
      underlying_code,
      DATE_TRUNC('month', expiry_date) AS expiry_month,
      MAX(expiry_date) AS expiry_date,
      AVG(skewness) AS skewness
    FROM analysis.options_skewness_stats
    WHERE {{where_clause}}
      AND skewness IS NOT NULL
    GROUP BY date, underlying_code, DATE_TRUNC('month', expiry_date)
    ORDER BY date ASC, DATE_TRUNC('month', expiry_date) ASC
