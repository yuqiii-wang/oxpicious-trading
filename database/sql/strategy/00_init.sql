-- ============================================================================
--  Master Import Script — strategy/ (regenerated 2026-11-10: ONE TABLE = ONE
--  FILE; complete + FK-safe order; supersedes the stale hand list).
--  Usage: psql -d analytics -f strategy/00_init.sql
-- ============================================================================

\ir ../00_partition_utils.sql
-- schema files
\ir trade_decision_seqs/00_schema.sql

-- tables (FK order)
\ir algo_configs.sql
\ir training_runs.sql
\ir trade_decision_seqs/strategy_identity.sql
\ir trade_decision_seqs/strategy_results.sql
\ir trade_decision_seqs/strategy_risk_factors.sql
\ir trade_decision_seqs/strategy_risk_period.sql
\ir trade_decision_seqs/trade_decision.sql
\ir training_trials.sql
\ir trade_decision_seqs/strategy_daily.sql
\ir trade_decision_seqs/strategy_risks.sql

-- views / maintenance
\ir trade_decision_seqs/08_views.sql

