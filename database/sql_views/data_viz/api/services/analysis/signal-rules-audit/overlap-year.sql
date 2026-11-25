-- serves: GET /api/analysis/signals-audit/overlap?period=<year>
-- The pairwise family-overlap rows for ONE calendar-year period (the
-- audit heatmap's matrix + the pair detail table). Ordered pairs:
-- signal_type = base family A (row), other_signal_type = B (column);
-- overlap_pct = n_overlap / n_base is ASYMMETRIC by design.
-- args:
--   sec_type: value smallint — the audit's security scope
--   period: value smallint — the calendar year
--   signal_type_decode: fragment — labelDecodeSql("signal_type", "o.signal_type")
--   other_signal_type_decode: fragment — labelDecodeSql("signal_type", "o.other_signal_type")
SELECT o.period,
       o.period_start,
       o.period_end,
       o.signal_type,
       {{signal_type_decode}}             AS signal_type_label,
       o.other_signal_type,
       {{other_signal_type_decode}}       AS other_signal_type_label,
       o.n_base,
       o.n_other,
       o.n_universe,
       o.n_overlap,
       o.n_same_action,
       o.n_opposite_action,
       o.overlap_pct,
       o.expected_pct,
       o.jaccard,
       o.lift
FROM analysis_signals.signal_overlap_audit o
WHERE o.sec_type = {{sec_type}}::smallint
  AND o.period = {{period}}::smallint
ORDER BY o.signal_type, o.other_signal_type
