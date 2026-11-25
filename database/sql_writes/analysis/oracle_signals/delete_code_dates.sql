-- SQL write action — analysis.oracle_signals keyed DELETE: the
-- incremental diff's remove side (the rows the engine no longer
-- decides for one code — a pivot re-graded into omission, an upgraded
-- placement's old bar, ...).
-- Executed by analysis/oracle_signals/__main__.py
-- (OracleAnalysis._write_incremental)
--
-- Params (asyncpg $n, in order):
--   $1 sec_type  SMALLINT — static.label_dim 'sec_type' code
--   $2 code      TEXT — the security whose stale decisions go
--   $3 dates     date[] — the decision dates to delete
DELETE FROM analysis.oracle_signals
WHERE sec_type = $1 AND code = $2 AND date = ANY($3::date[])
