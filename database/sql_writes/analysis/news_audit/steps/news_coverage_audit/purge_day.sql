-- SQL write action — text.news_coverage_audit purge (one trading day).
-- Executed by analysis/news_audit/steps.py (CoverageAuditStep) before the
-- recomputed rows of a target day are upserted.
--
-- Params (asyncpg $n, in order):
--   $1 date  DATE — the trading day being recomputed
DELETE FROM text.news_coverage_audit
WHERE date = $1
