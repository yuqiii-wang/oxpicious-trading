-- ============================================================================
--  Refresh is_active on analysis_signals.signal_strategies ($1 = sec_type).
--
--  TRUE only on each (code, sec_type, signal_type, signal_sub_type)'s
--  LATEST end_date row — the freshest forecast snapshot owning that
--  config; FALSE everywhere else. Executed by
--  python -m analyze.analysis_signals after EVERY run (the live tier
--  and the UI config menu read the active rows as the current
--  threshold set).
-- ============================================================================

UPDATE analysis_signals.signal_strategies s
SET is_active = (s.end_date = latest.max_end)
FROM (
    SELECT code, signal_type, signal_sub_type, MAX(end_date) AS max_end
    FROM analysis_signals.signal_strategies
    WHERE sec_type = $1
    GROUP BY code, signal_type, signal_sub_type
) latest
WHERE s.sec_type = $1
  AND s.code = latest.code
  AND s.signal_type = latest.signal_type
  AND s.signal_sub_type = latest.signal_sub_type;
