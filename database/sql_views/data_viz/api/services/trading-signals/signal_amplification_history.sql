-- ONE code's signal AMPLIFICATION history (the ORIGINAL RECORD KEEPER
-- behind the Signal Amplification page's records table): one row per
-- amplifier day (code, sec_type, amplifier_type, sub_type, date — the
-- two-level amplifier axis) with the day's anatomy facts and the
-- per-bound-family amplification applied (bindings JSONB) + the
-- bar/fact payload (amplifier_params). Newest first. amplifier_type
-- decodes to the PARENT type label ('signal_amplifier_type'); sub_type
-- returns the RAW code — the service decodes the sub label host-side
-- via the per-parent TS mirror. The amplifier filter narrows to one
-- sub label (parent type + sub code pair). since optionally narrows
-- the window.
-- Serves GET /api/live-data/trading-signals/amplification-history.
-- args:
--   code: value text — the code whose amplifier days to list
--   sec_type_decode: fragment — labelDecodeSql("sec_type", "h.sec_type")
--   amplifier_decode: fragment — labelDecodeSql("signal_amplifier_type", "h.amplifier_type")
--   sec_type_code: fragment — LABEL_CODE.sec_type[secType]
--   amp_filter: fragment — "" or "AND h.amplifier_type = <type> AND h.sub_type = <sub>"
--   since_filter: fragment — "" or "AND h.date >= '<YYYY-MM-DD>'::date"
SELECT h.code,
            {{sec_type_decode}} AS sec_type,
            {{amplifier_decode}} AS amplifier_type,
            h.sub_type::int AS sub_type,
            to_char(h.date, 'YYYY-MM-DD') AS date,
            h.gap_pct::float8 AS gap_pct,
            h.wick_pct::float8 AS wick_pct,
            h.amt_ratio::float8 AS amt_ratio,
            h.close_pos::float8 AS close_pos,
            h.margin_vs_bar::float8 AS margin_vs_bar,
            h.depth::float8 AS depth,
            h.bindings,
            h.amplifier_params
     FROM analysis_signals.signal_amplification_history h
     WHERE h.sec_type = {{sec_type_code}} AND h.code = {{code}}
     {{amp_filter}}
     {{since_filter}}
     ORDER BY h.date DESC, h.amplifier_type, h.sub_type
