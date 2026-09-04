-- ============================================================================
--  Schema: analysis_signals
--
--  Per-DAY trading SIGNALS derived from the same extreme-day detection
--  logic the monthly forecast analysis (analysis_forecasts) uses, but
--  materialized at (code, date) granularity so they can be consumed
--  directly (UI / live trading):
--
--    - mov_rsi signals: a day is a signal when its rsi_{W}days sits in
--      the TOP 1% (action = sell, overbought) or BOTTOM 1% (action =
--      buy, oversold) of the code's trailing 5-year window ending at
--      the signal's own stat_month snapshot.
--    - mov_std signals: a day breaches the UPPER band
--      (price > ma_{W} + 2.0·std_{W}days → action = sell) or the LOWER
--      band (price < ma_{W} - 2.0·std_{W}days → action = buy).
--
--  Cooperation contract with analysis_forecasts:
--    signals are produced ONLY for the stat_months that
--    analysis_forecasts already has rows for (mov_rsi at pct = 1 /
--    mov_std at k = 2.0) — the forecasts' start month sets the first
--    signal date. Each month's detection uses the same trailing
--    5-year window (M - 5y, M], the same linear-interpolated
--    percentile thresholds, the same cooldown suppression and the
--    same full-window history gate as the forecast buckets, and a
--    signal date is emitted only within its own snapshot month M
--    (one snapshot owns each date → no cross-month PK conflicts).
--
--  Population convention:
--    - `python -m analyze.analysis_signals` runs incrementally at
--      month granularity (only stat_months missing from
--      analysis_signals.signals are computed; months are written
--      atomically in one transaction).
--    - `--force` deletes the sec_type's signal rows and recomputes
--      every stat_month present in analysis_forecasts.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS analysis_signals;

COMMENT ON SCHEMA analysis_signals IS 'Per-day trading signals (buy/sell) mirroring the analysis_forecasts extreme-day detection at signal granularity: mov_rsi top/bottom-1% RSI days and mov_std 2-sigma Bollinger breaches, one row per (code, sec_type, signal_type, signal_sub_type, date). Populated incrementally by python -m analyze.analysis_signals, month-gated to the stat_months present in analysis_forecasts.';
