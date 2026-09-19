-- ============================================================================
--  Master Import Script
--  Execute all split SQL files in order.
--  Usage: psql -d strategy -f 00_init.sql
-- ============================================================================

\ir ../00_partition_utils.sql
\ir trade_decision_seqs/00_schema.sql
\ir trade_decision_seqs/01_strategy_identity.sql
\ir trade_decision_seqs/02_strategy_results.sql
\ir trade_decision_seqs/03_trade_decision.sql
\ir trade_decision_seqs/04_strategy_daily.sql
\ir trade_decision_seqs/05_strategy_risks.sql
\ir trade_decision_seqs/06_strategy_risk_period.sql
\ir trade_decision_seqs/07_strategy_risk_factors.sql
\ir trade_decision_seqs/08_views.sql
\ir 04_factors_and_algos.sql
\ir 05_training_process.sql
