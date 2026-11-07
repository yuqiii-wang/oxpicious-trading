-- Pair Pct-Change Ratios — the hedge ratios (both modes, four windows
-- each) for ONE industry x industry pair against ONE offset market
-- (PK-prefix scan: code, benchmark_code, benchmark_market, interval,
-- grain), ordered by date.
-- Serves GET /api/analysis/pair-pct-ratios
-- args:
--   code: value text — the subject industry_id
--   benchmark: value text — the benchmark industry_id
--   market: value text — the offset market index code
--   stride: value int — the stride-grid interval
--   grain: value smallint — encodeLabelParam("pair_grain", grain)
    SELECT date,
           hedge_raw_ma5d, hedge_raw_ma20d, hedge_raw_ma60d,
           hedge_raw_ma255d,
           hedge_offset_ma5d, hedge_offset_ma20d, hedge_offset_ma60d,
           hedge_offset_ma255d
    FROM analysis.pair_pct_change_ratios
    WHERE code = {{code}} AND benchmark_code = {{benchmark}}
      AND benchmark_market = {{market}} AND "interval" = {{stride}} AND grain = {{grain}}
    ORDER BY date ASC
