-- serves: GET /api/analysis/signals-audit/summary?period=all
-- The per-family census summed over ALL stored year periods (year
-- buckets are disjoint, so counts, universe and the date bounds
-- re-aggregate exactly; n_codes / n_days are NOT additive — they are
-- recomputed from the standalone trading population itself).
-- args:
--   sec_type: value smallint — the audit's security scope
--   signal_type_decode: fragment — labelDecodeSql("signal_type", "p.signal_type")
WITH per_type AS (
    SELECT p.signal_type,
           sum(p.n_events)::bigint           AS n_events,
           sum(p.n_buy)::bigint              AS n_buy,
           sum(p.n_sell)::bigint             AS n_sell,
           sum(p.signal_universe)::bigint    AS signal_universe,
           min(p.period_start)               AS period_start,
           max(p.period_end)                 AS period_end
    FROM analysis_signals.signal_type_audit p
    WHERE p.sec_type = {{sec_type}}::smallint
    GROUP BY p.signal_type
),
spread AS (
    SELECT h.signal_type,
           count(DISTINCT h.code)::int   AS n_codes,
           count(DISTINCT h.date)::int   AS n_days,
           min(h.date)                   AS first_date,
           max(h.date)                   AS last_date
    FROM analysis_signals.history_signals h
    WHERE h.sec_type = {{sec_type}}::smallint
      AND h.is_in_portfolio = FALSE
      AND h.confidence >= 0
      AND h.action IN (0, 1)
    GROUP BY h.signal_type
)
SELECT p.period_start,
       p.period_end,
       p.signal_type,
       {{signal_type_decode}}      AS signal_type_label,
       p.n_events,
       p.n_buy,
       p.n_sell,
       s.n_codes,
       s.n_days,
       p.signal_universe,
       s.first_date,
       s.last_date
FROM per_type p
JOIN spread s ON s.signal_type = p.signal_type
ORDER BY p.n_events DESC, p.signal_type
