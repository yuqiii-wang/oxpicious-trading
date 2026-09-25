-- ============================================================================
--  Rank signal_order on analysis_signals.signal_strategies ($1 = sec_type).
--
--  1-based best-first rank WITHIN each (sec_type, end_date) pool by
--  confidence DESC (the chosen rung's sign-aligned dir_ave — the
--  expected favorable blended move), PK tuple as the deterministic
--  tiebreak.
--  Nothing is trimmed — every gate-passing strategy keeps its rank.
--  Executed by python -m analyze.analysis_signals after EVERY run
--  (over the whole sec_type: the refresh-month rewrites can change the
--  pool's confidence ordering).
--
--  NOT score-ordered (2026-09 market-regimes study,
--  docs/market_regimes_study.md): the walk-forward regime-weighted
--  score (confidence × analysis_forecasts.regime_weights) was tested
--  head-to-head against this confidence ordering and did NOT beat it
--  out-of-sample (pooled top-1% 1.50% equal vs 1.36% weighted; monthly
--  win rate 23/55) — the regime split itself carries the value, the
--  in-window metric already encodes the regime lift, and the weights
--  ship as evidence/display (analysis_forecasts.regime_weights + the
--  forecasts UI), not as the ordering key.
-- ============================================================================

WITH ranked AS (
    SELECT code, signal_type, signal_sub_type, start_date, end_date,
           ROW_NUMBER() OVER (
               PARTITION BY end_date
               ORDER BY confidence DESC NULLS LAST,
                        code, signal_type, signal_sub_type, start_date
           ) AS rn
    FROM analysis_signals.signal_strategies
    WHERE sec_type = $1
)
UPDATE analysis_signals.signal_strategies s
SET signal_order = ranked.rn
FROM ranked
WHERE s.sec_type = $1
  AND s.code = ranked.code
  AND s.signal_type = ranked.signal_type
  AND s.signal_sub_type = ranked.signal_sub_type
  AND s.start_date = ranked.start_date
  AND s.end_date = ranked.end_date;
