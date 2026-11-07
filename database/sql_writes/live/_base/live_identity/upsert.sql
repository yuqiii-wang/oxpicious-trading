-- SQL write action — live.live_identity UPSERT (the pipeline registration
-- row). Executed by live/_base.py (LivePipeline.upsert_live_identity) on
-- every live.* entry-point run; convention of this tree:
--   database/sql_writes/<app>/<layer>/<table>/<action>.sql
--
-- Params (asyncpg $n, in order):
--   $1 name          TEXT — the registration key (one row per pipeline)
--   $2 detail_name   TEXT — the run's mode/detail label
--   $3 summary_name  TEXT | NULL — the UI-facing summary label
--   $4 description   TEXT — what the pipeline computes
--
-- The row's last_run_datetime doubles as the UI's "last ran" stamp.
INSERT INTO live.live_identity
    (name, detail_name, summary_name, last_run_datetime, description)
VALUES ($1, $2, $3, NOW(), $4)
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description
