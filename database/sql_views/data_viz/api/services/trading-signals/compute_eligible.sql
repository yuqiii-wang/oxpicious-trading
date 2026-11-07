-- Can the on-demand as-of evaluation run for (sec_type, date)? TRUE iff
-- the sec_type carries RULE CELLS (a live signal_rules scope to replay)
-- AND the date carries daily closes (an evaluation basis).
-- Serves GET /api/live-data/trading-signals (empty-day on-demand path).
-- args:
--   date: value text — the requested day, 'YYYY-MM-DD'
--   sec_type_code: fragment — LABEL_CODE.sec_type[secType]
--   basic_stats_table: fragment — BASIC_STATS_TABLE[secType]
SELECT (
        EXISTS (SELECT 1 FROM analysis_signals.signal_rules
                WHERE sec_type = {{sec_type_code}})
        AND EXISTS (SELECT 1 FROM {{basic_stats_table}}
                    WHERE date = {{date}}::date AND close IS NOT NULL)
     ) AS eligible
