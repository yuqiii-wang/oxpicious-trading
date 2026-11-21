-- SQL write action — text.news_provider_audit purge (one window).
-- Executed by analysis/news_audit/steps.py (ProviderAuditStep) before the
-- recomputed rows of a target window are upserted.
--
-- Params (asyncpg $n, in order):
--   $1 window_start  DATE — the grid window being recomputed
DELETE FROM text.news_provider_audit
WHERE window_start = $1
