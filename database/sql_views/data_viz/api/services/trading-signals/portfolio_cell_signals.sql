-- The sourced member CELL-LEVEL records behind a page of PORTFOLIO events:
-- every history_signals row matching the events' cell_signals PK keys
-- (joined through jsonb_to_recordset over the caller-built key array) —
-- each fetched row REPLACES the event-time snapshot as the member's
-- authoritative record. Legacy keys may carry the retired
-- is_ohlcv_boosted payload field; the resolver ignores it.
-- Serves GET /api/live-data/trading-signals/portfolio-history.
-- args:
--   cell_keys: value jsonb text — JSON array of the events' cell_signals
--     PK keys (code/signal_type/signal_sub_type/is_in_portfolio/date/time)
--   action_decode: fragment — labelDecodeSql("action", "h.action")
--   sec_type_code: fragment — LABEL_CODE.sec_type[secType]
SELECT h.code,
              h.signal_type, h.signal_sub_type,
              {{action_decode}} AS action,
              to_char(h.date, 'YYYY-MM-DD') AS date,
              to_char(h.time, 'HH24:MI')    AS time,
              h.signal::float8              AS signal,
              h.signal_reference::float8    AS signal_reference,
              h.confidence,
              h.is_in_portfolio
       FROM analysis_signals.history_signals h
       -- the stored cell_signals keys may carry the retired
       -- is_ohlcv_boosted payload field (rows predating the
       -- 2026-11-01 axis unlink) — the resolver ignores it; every
       -- surviving event IS the plain population
       JOIN jsonb_to_recordset({{cell_keys}}::jsonb) AS k(code text,
              signal_type smallint, signal_sub_type smallint,
              is_in_portfolio boolean,
              date date, time text)
         ON h.code = k.code
        AND h.signal_type = k.signal_type
        AND h.signal_sub_type = k.signal_sub_type
        AND h.is_in_portfolio = COALESCE(k.is_in_portfolio, false)
        AND h.date = k.date
        AND h.time = k.time::time
       WHERE h.sec_type = {{sec_type_code}}
