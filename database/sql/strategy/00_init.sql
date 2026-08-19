-- ============================================================================
--  Master Import Script
--  Execute all split SQL files in order.
--  Usage: psql -d strategy -f 00_init.sql
-- ============================================================================

\ir 01_trade_decision_seqs.sql
\ir 03_forecast_1m.sql
\ir 04_factors_and_algos.sql
\ir 05_training_process.sql