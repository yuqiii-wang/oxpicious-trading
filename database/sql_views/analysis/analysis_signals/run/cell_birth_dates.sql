-- The SIGNAL-CLUSTER PROBATION's cell-birth map (2026-11-18): each
-- measured cell's FIRST-EVER history_signal_performance snapshot date
-- — the cell's system birth on the never-purged snapshot record (the
-- reject tier's engines/reject.py probation reads it: a cell born
-- within REJECT_FIRST_APPEARANCE_DAYS of the data horizon is still
-- fresh and may not reject yet). Scoped to ONE family run
-- (sec_type x signal_type); the snapshot PK carries no is_in_portfolio,
-- so the standalone and portfolio-sourced twins share one birth per
-- (sub_type, action, code, trend, regime_state).
-- args:
--   sec_type: value smallint — the family's sec_type code
--   signal_type: value smallint — the family's signal_type code
SELECT signal_type, signal_sub_type, code, trend, regime_state, action,
       min(date) AS born
FROM analysis_signals.history_signal_performance
WHERE sec_type = {{sec_type}}
  AND signal_type = {{signal_type}}
GROUP BY signal_type, signal_sub_type, code, trend, regime_state, action
