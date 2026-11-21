-- Distinct 5-day grid windows that actually contain news. Windows with
-- zero articles are permanently row-less in the audit tables (a groupby
-- emits nothing for them), so incremental missing-window detection must
-- treat them as covered — otherwise every run re-targets the corpus's
-- sparse early span and re-fetches full history.
-- No args.
SELECT DISTINCT n.date - ((n.date - DATE '2000-01-03') % 5) AS window_start
FROM text.news n
