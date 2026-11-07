-- SQL write action — live.live_identity UPSERT (the prune-bookkeeping
-- row). Executed by live/_base.py (LivePipeline.upsert_prune_bookkeeping_row)
-- at the end of each retention prune: its last_run_datetime marks the
-- trading date the rolling-window prune last ran for — the probe target
-- of maybe_prune_for_new_day's once-per-new-day guard (the unindexed
-- DELETE date scan must not run on every 5-min tick).
--
-- Params (asyncpg $n, in order):
--   $1 name          TEXT — the bookkeeping row's key
--   $2 description   TEXT — what the prune covers
--
-- Unlike the registration upsert this touches last_run_datetime ONLY
-- (detail/summary/description keep their original insert values).
INSERT INTO live.live_identity
    (name, detail_name, summary_name, last_run_datetime, description)
VALUES ($1, NULL, NULL, NOW(), $2)
ON CONFLICT (name) DO UPDATE SET last_run_datetime = NOW()
