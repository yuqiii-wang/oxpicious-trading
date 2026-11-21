-- Volatility — one security's full daily volatility series from
-- analysis.volatility (all *vol* columns percent annualized, atr_14
-- price level; early rows NULL through estimator warm-up, the rv leg
-- NULL outside the intraday-bar retention window), joined with the
-- session's raw close from the sec_type's own bars source (the same
-- table the pipeline reads — every vol row's date exists there, so the
-- join never drops rows; close feeds the UI's price overlay line).
-- The UI slices via dataZoom — the whole history is served.
-- Serves GET /api/analysis/volatility/series
-- args:
--   codes: value text[] — codeVariants(target) (bare + suffixed forms)
--   sec_type: value smallint — LABEL_CODE.sec_type[secType]
--   bars_table: fragment — BARS_TABLE[secType] (stats.*_basic_stats)
    SELECT
      v.date,
      b.close,
      v.hv_5,
      v.hv_10,
      v.hv_20,
      v.hv_60,
      v.hv_120,
      v.hv_252,
      v.ewma_vol,
      v.parkinson_vol_20,
      v.garman_klass_vol_20,
      v.rogers_satchell_vol_20,
      v.yang_zhang_vol_20,
      v.overnight_vol_20,
      v.intraday_vol_20,
      v.atr_14,
      v.natr_14,
      v.boll_bw_20,
      v.vol_of_vol_20,
      v.vol_ratio_20_252,
      v.hv_20_pct_rank_252,
      v.ewma_vol_pct_rank_252,
      v.yang_zhang_vol_20_pct_rank_252,
      v.natr_14_pct_rank_252,
      v.hv_20_z_252,
      v.ewma_vol_z_252,
      v.rv_5min,
      v.jump_ratio_5min,
      v.rv_bar_count
    FROM analysis.volatility v
    LEFT JOIN {{bars_table}} b
      ON b.code = v.code AND b.date = v.date
    WHERE v.sec_type = {{sec_type}}
      AND v.code = ANY({{codes}}::text[])
    ORDER BY v.date ASC
