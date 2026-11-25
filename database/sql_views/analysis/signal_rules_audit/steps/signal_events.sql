-- Standalone trading signal events for the signal rules audit: one row
-- per (code, date, signal_type) DAY OBSERVATION with per-side flags.
-- Population = analysis_signals.history_signals STANDALONE TRADING tier
-- (no portfolio-sourced rows, no wait twins, no REJECT tier); sub types
-- collapse — a family firing several cells the same (code, date) is one
-- observation with has_buy / has_sell flags. Dates ride as epoch float8
-- (the cudf DB-read convention — python-date columns poison frames).
-- args:
--   sec_type: value smallint — the audit's security scope (0=etf 1=index 2=stock)
SELECT h.code,
       EXTRACT(EPOCH FROM h.date)::float8 AS date_epoch,
       h.signal_type,
       (bool_or(h.action = 0))::int4 AS has_buy,
       (bool_or(h.action = 1))::int4 AS has_sell
FROM analysis_signals.history_signals h
WHERE h.sec_type = {{sec_type}}::smallint
  AND h.is_in_portfolio = FALSE
  AND h.confidence >= 0
  AND h.action IN (0, 1)
GROUP BY h.code, h.date, h.signal_type
