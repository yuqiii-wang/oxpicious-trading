-- ============================================================================
--  Master Import Script
--  Execute all split SQL files in order.
--  Usage: psql -d strategy -f 00_init.sql
-- ============================================================================

\ir 01_trade_decision_seqs.sql