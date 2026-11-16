-- ============================================================================
--  SQL statement audit — one row per DISTINCT statement executed on the
--  cluster, keyed by a hash of the SQL text, from pg_stat_statements.
--
--  database/sql_audit.py (always-on, default --interval 300s,
--  heartbeat-registered + watchdog-restarted like database.db_health)
--  samples BOTH instances' pg_stat_statements (the Supabase image ships it
--  preloaded on the primary; database/replica/entrypoint.sh appends the
--  preload on the replica so the API's read half — served there — is
--  audited too) and upserts one row per statement: sql_hash is the sha256
--  hex of the extension's NORMALIZED statement text (literals replaced by
--  $n placeholders), so the same templated query with different params is
--  ONE audited identity no matter which role/instance/connection ran it.
--
--  Stats are DELTA-ACCUMULATED by the sampler: each pass diffs the
--  extension's cumulative counters (calls, total_exec_time, rows) against
--  its cached previous sample per (instance, queryid, role) and ADDS the
--  delta, so the row's totals survive extension resets and LRU evictions
--  (a counter DECREASE means a reset — the current value then IS the
--  delta since the last sample). Executions on the primary and the
--  replica are distinct real executions of the same text and are summed.
--
--  Cumulative audit — NO retention purge by design: rows are bounded by
--  the number of DISTINCT statements ever seen (pg_stat_statements.max
--  bounds the live set; evicted statements keep their audit row with
--  first_seen_at as their record). first_seen_at is the first SAMPLE that
--  saw the statement (its first execution can predate that by at most one
--  interval); last_active_at is the last sample with a positive call
--  delta (appearance in the view is NOT activity — rows persist until
--  evicted or reset).
--
--  Registered in 00_init.sql after service_heartbeat.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS monitor.sql_audit (
    sql_hash       char(64)     PRIMARY KEY,  -- sha256 hex of sql_text (the identity: same normalized text, same row)
    sql_text       text         NOT NULL,     -- pg_stat_statements' normalized statement ($n placeholders)
    queryid        bigint,                    -- PG's own normalized-parse-tree hash (cross-reference; changes across major upgrades, latest sample wins)
    roles          text         NOT NULL,     -- roles seen in the latest sample, comma-joined (e.g. postgres,readonly_analytics)
    instances      text         NOT NULL,     -- instances that ran it in the latest sample (primary / replica / primary,replica)
    call_count     bigint       NOT NULL DEFAULT 0,   -- cumulative executions since first_seen (sampler-accumulated deltas)
    total_exec_ms  numeric(18,3) NOT NULL DEFAULT 0, -- cumulative execution time
    avg_exec_ms    numeric(14,3) NOT NULL DEFAULT 0, -- total_exec_ms / call_count, recomputed each upsert
    max_exec_ms    numeric(14,3) NOT NULL DEFAULT 0, -- slowest single execution ever sampled
    rows_total     bigint       NOT NULL DEFAULT 0,  -- cumulative rows returned/affected
    first_seen_at  timestamptz  NOT NULL,     -- first sample that saw the statement
    last_active_at timestamptz  NOT NULL      -- last sample with a positive call delta
);

CREATE INDEX IF NOT EXISTS sql_audit_total_time_idx
    ON monitor.sql_audit (total_exec_ms DESC);

CREATE INDEX IF NOT EXISTS sql_audit_last_active_idx
    ON monitor.sql_audit (last_active_at DESC);

COMMENT ON TABLE monitor.sql_audit IS
    'SQL statement audit: one row per DISTINCT statement executed on the '
    'cluster (primary + replica), keyed by sha256 of the normalized '
    'statement text from pg_stat_statements — written (delta-accumulated, '
    'reset-surviving) by database/sql_audit.py. Same templated query with '
    'different parameters = one identity. No retention purge: bounded by '
    'distinct statements ever seen.';

COMMENT ON COLUMN monitor.sql_audit.sql_hash IS
    'sha256 hex (64 chars) of sql_text — the statement identity. The hash '
    'is over the text AS STORED (the extension''s normalization), so the '
    'identity is stable across roles, instances and parameter values.';

COMMENT ON COLUMN monitor.sql_audit.sql_text IS
    'pg_stat_statements'' normalized statement text: literals replaced by '
    '$n placeholders (parameterized and literal-constant variants of the '
    'same query collapse to one text). Long statements are stored in full '
    'by the extension (PG17 stores multi-KB texts).';

COMMENT ON COLUMN monitor.sql_audit.queryid IS
    'Postgres'' own normalized-parse-tree hash from pg_stat_statements, '
    'carried for cross-reference against the live view. NOT the identity '
    '(it is only stable within a server life / major version) — sql_hash '
    'is. Latest sample wins.';

COMMENT ON COLUMN monitor.sql_audit.roles IS
    'Roles that ran the statement in the sampler''s LATEST sample, '
    'comma-joined — a historical record of earlier roles is not kept '
    '(roles rarely change for a given text; postgres writes, '
    'readonly_analytics reads).';

COMMENT ON COLUMN monitor.sql_audit.call_count IS
    'Cumulative executions since first_seen, accumulated from per-pass '
    'deltas of pg_stat_statements.calls across roles and instances — '
    'survives extension resets and evictions (a counter decrease means a '
    'reset; the current value is then counted as the delta).';

COMMENT ON COLUMN monitor.sql_audit.total_exec_ms IS
    'Cumulative execution time (pg_stat_statements total_exec_time), '
    'delta-accumulated like call_count. avg_exec_ms is this divided by '
    'call_count at upsert time.';

COMMENT ON COLUMN monitor.sql_audit.last_active_at IS
    'The last sample pass in which the statement''s call counter '
    'ADVANCED — distinct from merely appearing in pg_stat_statements '
    '(rows persist there until evicted/reset). First-seen semantics '
    'mirror this on the other end: first_seen_at is the first sample '
    'that saw the statement, not its true first execution.';
