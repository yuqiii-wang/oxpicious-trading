-- ============================================================================
--  DB write-heavy alert log — the state-TRANSITION trail behind
--  monitor.db_table_audit.write_flag.
--
--  One row per flag CHANGE (not per pass — a table sitting red for an
--  hour at a 300s cadence writes ONE raise row, not twelve):
--    ok→amber / ok→red / amber→red   — a raise (severity 1 or 2)
--    red→amber / amber→ok / red→ok   — a clear or downgrade (severity of
--                                      the NEW state; 0 = cleared)
--  written by database/db_table_audit.py alongside its [ALERT]/[ok] log
--  lines. The CURRENT per-table flag is NOT derived from here — it is the
--  latest primary row's write_flag in monitor.db_table_audit; this table
--  is the human-readable monitor log / audit trail (when did it trip, how
--  hard, against which threshold, when did it clear). After a sampler
--  restart the in-memory previous-flag state re-baselines at ok, so a
--  still-red table logs one fresh raise row — the trail stays truthful.
--
--  severity is a SMALLINT label code (the label-code contract,
--  static.label_dim + _common/labels.py + data_viz/api/services/infra/
--  label-codes.ts): domain 'db_write_flag' — 0 ok | 1 amber | 2 red
--  (code order is semantic: best -> worst).
--
--  Retention: the sampler purges rows older than 90 days each pass
--  (handfuls of rows per day — cheap).
--
--  Registered in 00_init.sql after db_health.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS monitor.db_table_alert (
    id              bigserial    PRIMARY KEY,
    table_schema    text         NOT NULL,
    table_name      text         NOT NULL,
    severity        SMALLINT     NOT NULL,  -- label code (static.label_dim 'db_write_flag') of the NEW state: 0 ok (cleared) | 1 amber | 2 red
    write_rate      numeric(12,3) NOT NULL, -- the rate that tripped/cleared, rows/s over the window
    threshold_rate  numeric(12,3) NOT NULL, -- the threshold in effect: the amber/red line crossed on a raise, the amber line on a clear
    window_s        real         NOT NULL,  -- the audit window the rate was averaged over
    message         text         NOT NULL,  -- one-line human-readable summary (same text as the sampler's log line)
    raised_at       timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS db_table_alert_recent_idx
    ON monitor.db_table_alert (raised_at DESC);

CREATE INDEX IF NOT EXISTS db_table_alert_table_recent_idx
    ON monitor.db_table_alert (table_schema, table_name, raised_at DESC);

COMMENT ON TABLE monitor.db_table_alert IS
    'Append-only write-heavy alert trail: one row per monitor.db_table_audit'
    '.write_flag STATE TRANSITION (raise, downgrade, clear) by '
    'database/db_table_audit.py — severity (static.label_dim domain '
    'db_write_flag: 0 ok | 1 amber | 2 red) of the new state, the tripping '
    'rate and the threshold crossed. The CURRENT per-table flag lives on '
    'the latest primary audit row, not here.';

COMMENT ON COLUMN monitor.db_table_alert.severity IS
    'The NEW state''s static.label_dim (''db_write_flag'' domain) SMALLINT '
    'code: 2 red / 1 amber on a raise, 1 amber on a red→amber downgrade, '
    '0 ok on a clear — code order is semantic: best -> worst.';

COMMENT ON COLUMN monitor.db_table_alert.write_rate IS
    'The write_rate (insert+update+delete rows/s over window_s) that '
    'crossed threshold_rate on a raise, or fell back under it on a '
    'clear — the evidence row for the transition.';

COMMENT ON COLUMN monitor.db_table_alert.threshold_rate IS
    'The fixed threshold in effect at the transition: the amber or red '
    'line crossed on a raise, the amber line a clear fell under. The '
    'sampler''s constants (env-overridable) — raising them later does not '
    'rewrite history.';

COMMENT ON COLUMN monitor.db_table_alert.message IS
    'One-line human-readable summary of the transition — the same text the '
    'sampler logs ([ALERT] / [ok] lines in logs/<date>/db_table_audit.log).';
