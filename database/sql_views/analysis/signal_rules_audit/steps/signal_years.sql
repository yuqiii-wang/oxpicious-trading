-- Calendar years that carry standalone trading signal events at the
-- scope. Years absent here never materialize audit rows (a groupby
-- emits nothing for them), so incremental targeting must treat them as
-- covered — otherwise every run re-targets event-free years forever.
-- args:
--   sec_type: value smallint — the audit's security scope (0=etf 1=index 2=stock)
SELECT DISTINCT EXTRACT(YEAR FROM h.date)::int4 AS year
FROM analysis_signals.history_signals h
WHERE h.sec_type = {{sec_type}}::smallint
  AND h.is_in_portfolio = FALSE
  AND h.confidence >= 0
  AND h.action IN (0, 1)
