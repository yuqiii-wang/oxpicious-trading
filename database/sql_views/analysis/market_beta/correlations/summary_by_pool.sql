-- Correlations step sanity summary — row/pair counts + window date range
-- per pool_size slice of analysis.industry_correlations (log output only,
-- after the step's upsert).
-- args: none
        SELECT pool_size AS pool,
               COUNT(*) AS n_rows,
               COUNT(DISTINCT (industry_id, benchmark_industry_id))
                   AS n_pairs,
               MIN(start_date) AS first_date,
               MAX(start_date) AS last_date
        FROM analysis.industry_correlations
        GROUP BY pool_size
        ORDER BY pool_size
