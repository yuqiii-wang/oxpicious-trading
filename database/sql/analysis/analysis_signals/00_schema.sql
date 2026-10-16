-- ============================================================================
--  Schema: analysis_signals
--
--  The SIGNAL RULES tier (redesigned 2026-10-14): the former table set
--  (signals / signal_thresholds / signal_curves) was RETIRED — the
--  family knowledge now lives in ONE rules table and the event history
--  is POPULATED BY RUNNING THOSE RULES:
--
--    - signal_rules (01): one row per (code, sec_type, signal_type,
--      signal_sub_type, trend, regime_state, action) — the rule CELL.
--      document carries the family knowledge doc (the repo's
--      static/example.*.json template stamped with the cell's measured
--      performance); rule_entry names the engine entry class that runs
--      the rule; rule_params the SELF-ADAPTIVE prepared parameters the
--      engine measured on the code's own history for THAT cell (the
--      breach bar, episode merge, gate + scale numbers).
--    - history_signals (02): one row per qualifying trigger day, the
--      event record (value, bar, scaled confidence) emitted by
--      replaying the rules over history.
--    - code_trend_days (03): one row per (code, date) — the TREND
--      classification of the prepared frame (trends.attach_trend),
--      shared by the trend-spans API (chart shading) and the live
--      tier's day-context rule-cell matching.
--
--  Population convention:
--    - `python -m analysis.analysis_signals` runs incrementally at
--      (code, signal_type) granularity — reads filter on the target
--      tables' primary keys; only stale scopes re-emit.
--    - `--force` deletes the sec_type's rows in both tables and
--      re-emits everything.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS analysis_signals;

-- The retired tiers (2026-10-14): thresholds + curves superseded by
-- signal_rules; `signals` the long-dead predecessor. Destructive —
-- fresh-init / migration only, never re-run on a populated database.
DROP TABLE IF EXISTS analysis_signals.signals CASCADE;
DROP TABLE IF EXISTS analysis_signals.signal_thresholds CASCADE;
DROP TABLE IF EXISTS analysis_signals.signal_curves CASCADE;

COMMENT ON SCHEMA analysis_signals IS 'Signal rules tier (redesigned 2026-10-14): signal_rules (one row per code x config x trend x regime x action cell — the family knowledge document, the engine entry, and the self-adaptive prepared params measured on the code''s own history) and history_signals (one row per qualifying trigger day, populated by replaying the rules). Populated by python -m analysis.analysis_signals.';
