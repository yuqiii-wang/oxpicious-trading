-- SQL write action — monitor.service_command INSERT (enqueue one downward
-- command for a running child). Executed by
-- server_cluster/_common/routers/processes.py (POST
-- /processes/{service}/command — the area servers' enqueue face of the
-- Phase 4 mailbox; the child's heartbeat thread claims AND consumes
-- each row atomically, so an enqueued row is delivered at most once).
--
-- Params (asyncpg $n, in order):
--   $1 service    TEXT     — the target child's heartbeat service name
--                            (the dotted module path)
--   $2 command    SMALLINT — the label code (static.label_dim domain
--                            service_command: 0 flush_now | 1 pause |
--                            2 resume)
--   $3 payload    JSONB    — optional arguments (NULL when none; e.g.
--                            {"max_pause_s": 600} on a pause — the
--                            child's own config bound still caps it)
--   $4 ttl_s      INTEGER  — the unconsumed expiry window (the 120s
--                            planned-stop-marker precedent): a command
--                            the child never polls dies at expires_at
--                            instead of firing late
INSERT INTO monitor.service_command (service, command, payload, expires_at)
VALUES ($1::text, $2::smallint, $3::jsonb,
        now() + make_interval(secs => $4::int))
