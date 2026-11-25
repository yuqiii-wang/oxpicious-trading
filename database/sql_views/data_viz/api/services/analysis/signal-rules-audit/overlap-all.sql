-- serves: GET /api/analysis/signals-audit/overlap?period=all
-- The pairwise family-overlap rows summed over ALL stored year periods.
-- Counts are additive (year buckets disjoint); the ratios re-derive
-- exactly from the sums: overlap = Σoverlap/Σbase, expected =
-- Σother/Σuniverse, jaccard = Σoverlap/(Σbase+Σother−Σoverlap),
-- lift = (Σoverlap·Σuniverse)/(Σbase·Σother).
-- args:
--   sec_type: value smallint — the audit's security scope
--   signal_type_decode: fragment — labelDecodeSql("signal_type", "o.signal_type")
--   other_signal_type_decode: fragment — labelDecodeSql("signal_type", "o.other_signal_type")
SELECT min(o.period_start)               AS period_start,
       max(o.period_end)                 AS period_end,
       o.signal_type,
       {{signal_type_decode}}            AS signal_type_label,
       o.other_signal_type,
       {{other_signal_type_decode}}      AS other_signal_type_label,
       sum(o.n_base)::bigint             AS n_base,
       sum(o.n_other)::bigint            AS n_other,
       sum(o.n_universe)::bigint         AS n_universe,
       sum(o.n_overlap)::bigint          AS n_overlap,
       sum(o.n_same_action)::bigint      AS n_same_action,
       sum(o.n_opposite_action)::bigint  AS n_opposite_action,
       (sum(o.n_overlap)::numeric / NULLIF(sum(o.n_base), 0)::numeric)
                                        AS overlap_pct,
       (sum(o.n_other)::numeric / NULLIF(sum(o.n_universe), 0)::numeric)
                                        AS expected_pct,
       (sum(o.n_overlap)::numeric
          / NULLIF(sum(o.n_base) + sum(o.n_other) - sum(o.n_overlap), 0)::numeric)
                                        AS jaccard,
       (sum(o.n_overlap)::numeric * sum(o.n_universe)::numeric
          / NULLIF(sum(o.n_base) * sum(o.n_other), 0)::numeric)
                                        AS lift
FROM analysis_signals.signal_overlap_audit o
WHERE o.sec_type = {{sec_type}}::smallint
GROUP BY o.signal_type, o.other_signal_type
ORDER BY o.signal_type, o.other_signal_type
