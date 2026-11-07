-- ONE portfolio code's FULL event history (newest first) — the PORTFOLIO
-- tier's coordinated events from analysis_signals.portfolio_history_signals
-- with their member association payload (member_signals / cell_signals
-- JSONB, expanded to member records by the service). Optional
-- signal_type / signal_sub_type narrow to one family / sub (validated
-- SMALLINT label codes — the family_predicates fragment suffix; unknown
-- labels filter to nothing in code before this query runs).
-- Serves GET /api/live-data/trading-signals/portfolio-history.
-- args:
--   code: value text — the portfolio code whose events to list
--   sec_type_code: fragment — LABEL_CODE.sec_type[secType]
--   family_predicates: fragment — " AND h.signal_type = <code>"-style
--     suffix predicates (" " when no family filter)
--   portfolio_type_decode: fragment — labelDecodeSql("portfolio_type", "h.portfolio_type")
--   portfolio_sub_type_decode: fragment — labelDecodeSql("portfolio_sub_type", "h.portfolio_sub_type")
--   signal_type_decode: fragment — labelDecodeSql("signal_type", "h.signal_type")
--   signal_sub_type_decode: fragment — labelDecodeSql("signal_sub_type", "h.signal_sub_type")
--   action_decode: fragment — labelDecodeSql("action", "h.action")
--   regime_decode: fragment — labelDecodeSql("regime", "h.regime_state")
SELECT h.code,
            {{portfolio_type_decode}} AS portfolio_type,
            {{portfolio_sub_type_decode}} AS portfolio_sub_type,
            {{signal_type_decode}} AS signal_type,
            {{signal_sub_type_decode}} AS signal_sub_type,
            to_char(h.date, 'YYYY-MM-DD') AS date,
            to_char(h.time, 'HH24:MI')    AS time,
            {{action_decode}} AS action,
            h.signal::float8               AS signal,
            h.signal_reference::float8     AS signal_reference,
            (h.signal - h.signal_reference)::float8 AS signal_excess,
            h.confidence,
            (h.confidence::float8 / 100.0) AS confidence_pct,
            {{regime_decode}} AS regime_state,
            h.is_typical,
            h.member_signals,
            h.cell_signals
     FROM analysis_signals.portfolio_history_signals h
     WHERE h.sec_type = {{sec_type_code}} AND h.code = {{code}}{{family_predicates}}
     ORDER BY h.date DESC, h.time DESC, h.signal_type, h.signal_sub_type
