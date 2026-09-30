-- ============================================================================
--  Rank signal_order on analysis_signals.signal_strategies ($1 = sec_type).
--
--  1-based best-first rank WITHIN each (sec_type, end_date,
--  ohlcv_type) pool by confidence DESC (the cohort's OWN chosen rung's
--  sign-aligned dir_ave — the expected favorable blended move), PK
--  tuple as the deterministic tiebreak. The ohlcv_type partition
--  (2026-09-28) keeps the derived-population cohorts SEPARATE — the
--  parent, the @boost and the @selection strategies never compete for
--  one rank space (each cohort is a view of the same buckets under a
--  different measurement; folding them into one confidence was
--  measured to degrade the OOS rank,
--  temp_scripts/study_ohlcv_signal_confidence.py). NULL groups
--  together (the parent cohort).
--  Nothing is trimmed — every gate-passing strategy keeps its rank.
--  Executed by python -m analyze.analysis_signals after EVERY run
--  (over the whole sec_type: the mutable-scope rewrites can change the
--  pool's confidence ordering).
--
--  EXACT ROW JOIN (the 2026-09-28 fix): the ranked CTE carries the
--  strategy's FULL PK — side + regime_state included — so each ranked
--  row joins back to exactly ONE target row. The previous join keyed
--  on (code, signal_type, signal_sub_type, ohlcv_type, start_date,
--  end_date) only, which side/regime siblings of one config share:
--  every sibling matched every other's ranked row and the UPDATE
--  assigned an arbitrary sibling's rank (multi-regime buckets of one
--  config landed on scrambled, often duplicated ranks). The tiebreak
--  also names side + regime_state, so equal-confidence siblings get a
--  deterministic, stable order instead of an arbitrary one.
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
    SELECT code, signal_type, signal_sub_type, ohlcv_type,
           side, regime_state, start_date, end_date,
           ROW_NUMBER() OVER (
               PARTITION BY end_date, ohlcv_type
               ORDER BY confidence DESC NULLS LAST,
                        code, signal_type, signal_sub_type, side,
                        regime_state, start_date
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
  AND s.ohlcv_type IS NOT DISTINCT FROM ranked.ohlcv_type
  AND s.side = ranked.side
  AND s.regime_state = ranked.regime_state
  AND s.start_date = ranked.start_date
  AND s.end_date = ranked.end_date;
