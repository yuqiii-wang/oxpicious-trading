-- The managed PORTFOLIO codes of a sec_type with their census (one row per
-- PORTFOLIO RULE — code x portfolio_sub_type since the 2026-10-25 split):
-- member/valid cell counts, coordinated sub types, the code's latest
-- replayed event date and the emitting pipeline run. The WHERE predicates
-- ride as one fragment (sec_type always; portfolio_type optionally — an
-- unknown label filters to nothing in code before this query runs).
-- Serves GET /api/live-data/trading-signals/portfolios.
-- args:
--   portfolio_type_decode: fragment — labelDecodeSql("portfolio_type", "p.portfolio_type")
--   portfolio_sub_type_decode: fragment — labelDecodeSql("portfolio_sub_type", "p.portfolio_sub_type")
--   sec_predicates: fragment — the WHERE predicate list (predicates.join(" AND "))
SELECT p.code,
            {{portfolio_type_decode}} AS portfolio_type,
            {{portfolio_sub_type_decode}} AS portfolio_sub_type,
            cardinality(p.signal_rule_ids)::int AS n_rules,
            (SELECT count(*)::int
             FROM analysis_signals.signal_rules r
             WHERE r.signal_rule_id = ANY(p.signal_rule_ids)
               AND r.is_valid) AS n_valid,
            COALESCE((SELECT count(*)::int
             FROM jsonb_object_keys(p.rule_params->'subs')), 0)
              AS n_sub_types,
            to_char(e.latest_event, 'YYYY-MM-DD') AS latest_event,
            to_char(p.created_at, 'YYYY-MM-DD HH24:MI') AS latest_built
     FROM analysis_signals.portfolio_signal_rules p
     LEFT JOIN LATERAL (
        SELECT max(h.date) AS latest_event
        FROM analysis_signals.history_signals h
        WHERE h.code = p.code AND h.sec_type = p.sec_type
     ) e ON TRUE
     WHERE {{sec_predicates}}
     ORDER BY p.code, p.portfolio_sub_type
