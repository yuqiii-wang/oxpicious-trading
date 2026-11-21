-- The run's codes' trailing typical-EMA rows (stats.{sec_type}_
-- tech_stats.ema12 / ema26 — the macd family's value source, the ONE
-- EMA construction in the repo), oldest-first. The forecast extends
-- the EMA recursion one synthetic day from the last real row, rebuilds
-- the MACD line, and runs the signal-line EMA9 over the fetched
-- window (the engine's compute_emas convention; the trailing window
-- fully converges the recursion — the fetch cap documents by how
-- much). stats.{sec}_tech_stats has no sec_type column (one table per
-- sec_type) — the table name fragment carries the scope.
-- args:
--   tech_stats_table: fragment — stats.{sec_type}_tech_stats
--   codes: value text[] — the run's codes
--   row_limit: value int — the per-code trailing row cap (EMA9
--     convergence window)
SELECT code, date, ema12, ema26 FROM (
    SELECT t.code AS code, t.date AS date,
           t.ema12::float8 AS ema12, t.ema26::float8 AS ema26,
           ROW_NUMBER() OVER (PARTITION BY t.code ORDER BY t.date DESC) AS rn
    FROM {{tech_stats_table}} t
    WHERE t.code = ANY({{codes}})
      AND t.ema12 IS NOT NULL AND t.ema26 IS NOT NULL
) ranked
WHERE rn <= {{row_limit}}
ORDER BY code, date
