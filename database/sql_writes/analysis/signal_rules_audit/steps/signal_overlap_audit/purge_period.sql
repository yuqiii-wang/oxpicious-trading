-- SQL write action — analysis_signals.signal_overlap_audit purge (one
-- period). Executed by analysis/signal_rules_audit/steps.py
-- (OverlapAuditStep) before the recomputed rows of a target period are
-- upserted — the upsert alone would leave stale rows for pairs that
-- disappeared from the period's recompute.
--
-- Params (asyncpg $n, in order):
--   $1 sec_type  SMALLINT — the audit's security scope
--   $2 period    SMALLINT — the calendar year being recomputed
DELETE FROM analysis_signals.signal_overlap_audit
WHERE sec_type = $1::smallint AND period = $2::smallint
