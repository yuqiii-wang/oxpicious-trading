-- serves: GET /api/analysis/signals-audit/meta
-- The audit's period selector basis: every YEAR period present in the
-- per-family census at the scope, with its date bounds, the period's
-- signal universe (the chance denominator), family/event totals and the
-- last compute time. Periods are calendar years — counts additive, no
-- all-history rows exist (the all-period views SUM-re-aggregate).
-- args:
--   sec_type: value smallint — the audit's security scope (0=etf 1=index 2=stock)
SELECT t.period,
       min(t.period_start)                    AS period_start,
       max(t.period_end)                      AS period_end,
       max(t.signal_universe)::bigint         AS signal_universe,
       count(DISTINCT t.signal_type)::int     AS n_families,
       sum(t.n_events)::bigint                AS n_events,
       max(t.computed_at)                     AS computed_at
FROM analysis_signals.signal_type_audit t
WHERE t.sec_type = {{sec_type}}::smallint
GROUP BY t.period
ORDER BY t.period DESC
