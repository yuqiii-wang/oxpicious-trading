-- SQL write action — monitor.service_audit INSERT (one maintainer pass).
-- Executed by server_cluster/maintainer/jobs.py (PassAudit.insert, the
-- shared audit face: the daily 07:00 fleet bring-up and the 18:00
-- nightly pipeline chain each append one row per pass — the trail the
-- Service Monitor UI expands, same table the watchdog restarts write).
--
-- Params (asyncpg $n, in order):
--   $1 service       TEXT        — the pass's audit service name
--                                  (server_cluster.maintainer.bring_up |
--                                   server_cluster.maintainer.pipeline)
--   $2 pid           INTEGER     — the maintainer process's pid
--   $3 started_at    TIMESTAMPTZ — when the pass started (UTC)
--   $4 ended_at      TIMESTAMPTZ — when the pass ended (UTC)
--   $5 is_normal_exit BOOLEAN    — whether the pass completed cleanly
--   $6 exit_reason   TEXT        — the pass's human reason line
INSERT INTO monitor.service_audit
    (service, scope, sub_scope, pid, started_at, ended_at,
     is_normal_exit, exit_reason)
VALUES ($1::text, 'server_cluster', 'maintainer', $2::integer,
        $3::timestamptz, $4::timestamptz, $5::boolean, $6::text)
