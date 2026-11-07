-- The PORTFOLIO rules whose legs sit in the requested codes (the pair
-- types: BOTH split parts of the 'A~B' composite code must be in the list)
-- — the roster-census half of fetchPortfolioSignals, one row per
-- portfolio rule (code x portfolio_sub_type since the 2026-10-25 split).
-- Serves GET /api/live-data/trading-signals/portfolio.
-- args:
--   portfolio_sub_type_decode: fragment — labelDecodeSql("portfolio_sub_type", "p.portfolio_sub_type")
--   sec_type_code: fragment — LABEL_CODE.sec_type[secType]
--   portfolio_type_code: fragment — LABEL_CODE.portfolio_type.neg_corr_hedge_pair
--   codes: value text[] — the requested codes (deduped, trimmed)
SELECT p.code,
            {{portfolio_sub_type_decode}} AS portfolio_sub_type,
            to_char(e.latest_event, 'YYYY-MM-DD') AS latest_event,
            to_char(p.created_at, 'YYYY-MM-DD HH24:MI') AS latest_built
     FROM analysis_signals.portfolio_signal_rules p
     LEFT JOIN LATERAL (
        SELECT max(h.date) AS latest_event
        FROM analysis_signals.history_signals h
        WHERE h.code = p.code AND h.sec_type = p.sec_type
     ) e ON TRUE
     WHERE p.sec_type = {{sec_type_code}}
       AND p.portfolio_type = {{portfolio_type_code}}
       AND split_part(p.code, '~', 1) = ANY({{codes}}::text[])
       AND split_part(p.code, '~', 2) = ANY({{codes}}::text[])
     ORDER BY p.code, p.portfolio_sub_type
