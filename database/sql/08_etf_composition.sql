-- ============================================================================
--  ETF Composition — DEPRECATED
--  This table has been consolidated into stats.sec_composition (see 03_sec_composition.sql).
--  sec_composition now stores ALL holdings (not just top 5) with rank 1..N,
--  for both ETFs (source_type='etf') and CSI indices (source_type='index').
--
--  The DROP is safe and idempotent — if the table doesn't exist, it's a no-op.
--  All composition queries now go to stats.sec_composition.
-- ============================================================================

DROP TABLE IF EXISTS stats.etf_composition;
