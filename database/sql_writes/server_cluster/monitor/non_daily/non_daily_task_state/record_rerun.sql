-- SQL write action — monitor.non_daily_task_state re-run attempt
-- record. Executed by server_cluster/monitor/non_daily.py
-- (NonDailyOps._record_rerun — the Saturday backfill's per-attempt
-- bookkeeping after it submits the task through the run registry).
--
-- Params (asyncpg $n, in order):
--   $1 service        TEXT        — the re-run task's dotted module path
--   $2 next_check_at  TIMESTAMPTZ — the next weekly retry gate (+7d;
--                                   "if still none, one week is the query
--                                   interval, until all data is done loaded")
UPDATE monitor.non_daily_task_state
   SET attempts      = attempts + 1,
       last_rerun_at = now(),
       next_check_at = $2::timestamptz,
       updated_at    = now()
 WHERE service = $1::text
