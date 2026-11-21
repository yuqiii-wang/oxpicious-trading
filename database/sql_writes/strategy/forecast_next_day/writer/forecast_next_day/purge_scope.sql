-- SQL write action — strategy.forecast_next_day scope purge (the
-- pipeline's --force mode: drop the run's codes' rows before
-- regenerating them).
-- Executed by strategy/forecast_next_day/writer.py (ForecastWriter.purge_scope).
-- Params (asyncpg $n, in order): $1 sec_type SMALLINT (label code),
--   $2 codes TEXT[]
DELETE FROM strategy.forecast_next_day
WHERE sec_type = $1::smallint AND code = ANY($2::text[])
