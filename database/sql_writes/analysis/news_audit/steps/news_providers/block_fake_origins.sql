-- SQL write action — text.news_providers AUTO-block on audited fakes.
-- Executed by analysis/news_audit/steps.py (ProviderAutoBlockStep, no
-- params, single statement) after the window audits land: an origin whose
-- provider-audit history carries MORE THAN ONE fake item
-- (SUM(text.news_provider_audit.num_fake) > 1 over the origin's rows —
-- the lifetime of its digested verdicts) is flagged is_blocked = true,
-- which the news API's default filters use to exclude the origin's rows.
--
-- ONE-WAY curation: the statement never clears the flag — a manual
-- unblock of a >1-fake origin re-blocks on the next audit run. The
-- registry sync's upsert (upsert_origin.sql) still never touches
-- is_blocked; this action is the pipeline's only writer.
UPDATE text.news_providers np
   SET is_blocked = true,
       updated_at = now()
 WHERE np.is_blocked = false
   AND EXISTS (
       SELECT 1
         FROM text.news_provider_audit a
        WHERE a.source = np.source
          AND a.author = np.author
        HAVING SUM(a.num_fake) > 1
   )
