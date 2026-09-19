-- ============================================================================
--  Rank signal_order on analysis_signals.signal_strategies ($1 = sec_type).
--
--  1-based best-first rank WITHIN each (sec_type, end_date) pool by
--  confidence DESC (the gate's forecast confidence = the bucket's
--  mixed-row reverse_prob), PK tuple as the deterministic tiebreak.
--  Nothing is trimmed — every gate-passing strategy keeps its rank.
--  Executed by python -m analyze.analysis_signals after EVERY run
--  (over the whole sec_type: the refresh-month rewrites can change the
--  pool's confidence ordering).
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
