-- The matched PORTFOLIO rules' OWN rows (one per code x sub since the
-- 2026-10-25 split): the whole signal_rule_ids array, the construction +
-- per-sub coordination spec (rule_params), the rule-level gate verdict +
-- writing entries, and the LATEST history_portfolio_signal_performance
-- snapshot laterally joined newest-date-first per sub (the rule's audit).
-- Serves GET /api/live-data/trading-signals/portfolio.
-- args:
--   sec_type_code: fragment — LABEL_CODE.sec_type[secType]
--   portfolio_type_code: fragment — LABEL_CODE.portfolio_type.neg_corr_hedge_pair
--   codes: value text[] — the matched portfolio codes
SELECT p.code,
            p.portfolio_sub_type,
            p.signal_rule_ids,
            p.is_valid,
            p.rule_entry,
            p.rule_params,
            to_char(pp.pp_date, 'YYYY-MM-DD') AS pp_as_of,
            pp.pp_pooled,
            pp.pp_benefit,
            pp.pp_gate,
            pp.pp_dd
     FROM analysis_signals.portfolio_signal_rules p
     LEFT JOIN LATERAL (
        SELECT q.date AS pp_date, q.pooled AS pp_pooled,
               q.benefit AS pp_benefit, q.gate AS pp_gate,
               q.dd AS pp_dd
        FROM analysis_signals.history_portfolio_signal_performance q
        WHERE q.code = p.code AND q.portfolio_type = p.portfolio_type
          AND q.portfolio_sub_type = p.portfolio_sub_type
        ORDER BY q.date DESC LIMIT 1
     ) pp ON TRUE
     WHERE p.sec_type = {{sec_type_code}}
       AND p.portfolio_type = {{portfolio_type_code}}
       AND p.code = ANY({{codes}}::text[])
     ORDER BY p.code, p.portfolio_sub_type
