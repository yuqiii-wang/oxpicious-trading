-- The Trading Signals page menu: DISTINCT (signal_type, signal_sub_type)
-- of the sec_type's RULE CELLS (analysis_signals.signal_rules) that carry
-- at least one GATE-PASSING cell (action <> wait), excluding the options
-- families (they live on their own page). The stored sec_type/signal_type
-- columns are SMALLINT label codes — the filter carries the codes, the
-- response decodes back to the labels the UI speaks.
-- Serves GET /api/live-data/trading-signals/configs.
-- args:
--   signal_type_decode: fragment — labelDecodeSql("signal_type", "t.signal_type")
--   signal_sub_type_decode: fragment — labelDecodeSql("signal_sub_type", "t.signal_sub_type")
--   sec_type_code: fragment — LABEL_CODE.sec_type[secType]
--   options_wall_code: fragment — LABEL_CODE.signal_type.options_wall
--   options_vix_code: fragment — LABEL_CODE.signal_type.options_vix
--   wait_code: fragment — LABEL_CODE.action.wait
SELECT {{signal_type_decode}} AS signal_type,
            {{signal_sub_type_decode}} AS signal_sub_type,
            count(*)::int AS n_configs
     FROM analysis_signals.signal_rules t
     WHERE t.sec_type = {{sec_type_code}}
       -- the options families live on their OWN page (/analysis/
       -- signals/options) — the shared config menu stays the three
       -- general families (mov_rsi / bollinger / ma_cross)
       AND t.signal_type <> {{options_wall_code}}
       AND t.signal_type <> {{options_vix_code}}
       -- wait rows are observational only since 2026-10-19 (the
       -- ungated VRP twin; the starvation wait era ended — under-floor
       -- cells trade at a reduced confidence): the menu contract stays
       -- "at least one GATE-PASSING cell"
       AND t.action <> {{wait_code}}
     GROUP BY t.signal_type, t.signal_sub_type
     ORDER BY t.signal_type, t.signal_sub_type
