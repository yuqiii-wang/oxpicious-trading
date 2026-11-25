-- Hypes & drains step — incremental missing-date detection. Source dates
-- (the guard's scope: broad-market industry_attributions rows with daily
-- non-industry price data) that are NOT yet covered by a per-date ranking
-- in analysis.industry_hypes_and_drains. The floor at the hypes table's
-- own MIN(date) excludes the pre-first-window source dates that
-- legitimately emit nothing (no period has a full trailing window yet);
-- an EMPTY hypes table makes the floor NULL and the join drops every row
-- — the caller treats an empty result + empty table as a full build.
-- A source date whose compute legitimately emits no rank rows would be
-- re-detected on every run (harmless: the recompute is a no-op INSERT of
-- zero rows; measure-zero at real data).
-- args: none
    WITH src AS (
        SELECT DISTINCT ia.date
        FROM analysis.industry_attributions ia
        JOIN stats.sec_index_tags sit ON sit.code = ia.benchmark_code
        WHERE sit.is_broad_market = TRUE
          AND ia.attribution_type = 'trading_amt'
          AND ia.benchmark_non_this_industry_price IS NOT NULL
    ),
    coverage AS (
        SELECT MIN(date) AS min_date
        FROM analysis.industry_hypes_and_drains
    )
    SELECT s.date
    FROM src s
    CROSS JOIN coverage c
    WHERE c.min_date IS NOT NULL
      AND s.date >= c.min_date
      AND NOT EXISTS (
          SELECT 1 FROM analysis.industry_hypes_and_drains hd
          WHERE hd.date = s.date)
    ORDER BY s.date
