-- ============================================================================
--  Table: static.label_dim  (closed-vocabulary label <-> SMALLINT code map)
--
--  The DB-side source of truth for every SMALLINT-coded label column
--  (the label-code contract): a converted column stores the CODE, this
--  table maps code -> label, and the boundary mirrors carry the same
--  mapping — Python _common/labels.py (pipelines), TypeScript
--  data_viz/api/services/label-codes.ts (API decode/params).
--  temp_scripts/check_label_dim.py asserts all three stay identical.
--
--  LAYOUT: this file carries the DDL only. The data rows live in the
--  purpose-scoped sibling INSERT files, one per business area:
--    10_market_state          regime / side / px_speed / vol_state
--    20_signal_detection      signal_type / signal_sub_type / action /
--                             analysis_cache_kind
--    30_industry_aggregation  weighting / rank_side / pool_size /
--                             attribution_type
--    40_security_identity     sec_type / board / sec_owner_type /
--                             contract_type / option_type / skew_type
--    50_trading               trade_side
--    60_monitoring_infra      ext_resource / ext_http_source /
--                             ext_llm_provider / db_instance /
--                             db_replication_state
--  Adding a domain = add its rows to the matching purpose file (and
--  the mirrors above).
--
--  WHY codes: label strings riding cudf.pandas frames through
--  sort/merge/concat chains fault at host transfer on cuDF 26.08
--  (cudaErrorIllegalAddress — string offsets/chars view corruption);
--  int16 codes are cudf-native through every op. Strings are decoded
--  ONLY at the API boundary and for display/logging — never inside a
--  compute frame.
--
--  Converted columns (2026-10-05 refactor; 2026-10-06 second wave;
--  2026-10-07 option_type):
--    regime     stats.market_regimes, stats.market_regime_spans,
--               analysis_signals.signal_thresholds / history_signals
--               (as regime_state), live.live_signals (as regime_state)
--    side       analysis_signals.signal_thresholds / signal_curves,
--               analysis.mov_ave_price_vs_amt (0 top / 1 bottom / 2 flat)
--    action     analysis_signals.signal_thresholds / history_signals,
--               live.live_signals
--    weighting  analysis.industry_hypes_and_drains / _seasonal
--    rank_side  analysis.industry_hypes_and_drains / _seasonal
--    pool_size  stats.industry_basic_stats, analysis.industry_correlations,
--               analysis.industry_etf_contribution,
--               analysis_composites.industry_corr_benchmark_offsets
--    px_speed   analysis.mov_ave_price_vs_amt
--    vol_state  analysis.mov_ave_price_vs_amt
--    board      stats.stock_identity (NULLable), stats.sec_board_map
--    sec_owner_type  stats.sec_owners.type (NULLable)
--    contract_type  stats.futures_identity,
--               analysis.futures_gap_quintiles
--    option_type  stats.options_terms,
--               analysis.options_expiry_identity / options_skewness_stats /
--               options_oi_stats / options_iv_skew_stats / options_walls
--    signal_type
--               analysis_signals.signal_thresholds / history_signals /
--               signal_curves (0 mov_rsi / 1 bollinger; converted
--               2026-10-10)
--    sec_type   the analysis.mov_ave* family (mov_ave_spreads_detail/_ema/
--               _ohlc_gaps, roof_and_floor_lines/_bands, trading_amt/
--               _ratios, high_low_pct/_streaks, rsi/_ranks/_holiday,
--               price_vs_amt, spreads_codes) AND the analysis.pe /
--               pe_codes / pe_pct / pe_pct_streaks / dividends /
--               dividend_est / dividend_deliveries / margin_tech_stats /
--               margin_changes tables, and monitor.src_data_audit.
--               analysis_signals.* (2026-10-10); OTHER sec_type columns (stats.*,
--               live.*) KEEP TEXT — see NOT converted below.
--    ext_resource / ext_http_source / ext_llm_provider
--               monitor.external_resource_audit (resource_type /
--               sub_type): the external request audit's type columns.
--               sub_type's domain is selected by resource_type (http ->
--               ext_http_source, llm -> ext_llm_provider).
--    db_instance / db_replication_state
--               monitor.db_health (instance / replication_state): the DB
--               health sampler's instance identity and the WAL shipping
--               state as seen from the sampled instance.
--    analysis_cache_kind
--               analysis_signals.analysis_cache (kind): the generic
--               intermediate-result cache's tenant discriminator.
--
--  PENDING (vocabulary registered, columns STILL TEXT — convert table +
--  writers before using the codes): skew_type
--  (analysis.options_skewness_stats / live.options_intraday_skewness),
--  attribution_type (analysis.industry_attributions), trade_side
--  (strategy.trade_decision).
--
--  NOT converted (deliberately): sec_type OUTSIDE the analysis schema's
--  converted tables (API-facing identity + filter columns across
--  stats/live), industry_id / sector_id / exchange
--  (API-facing identity + filter columns; exchange additionally blocked
--  by a partition-key table), signal_sub_type
--  (open parameterized family identifier), scale_method (reserved
--  vocabulary), and the text.* LLM-layer enums. In cudf frames sec_type / industry_id
--  ride as INTERNAL int16 codes materialized back at the Python write
--  boundary — _common.labels.SEC_TYPE_CODE.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS static;

DROP TABLE IF EXISTS static.label_dim CASCADE;

CREATE TABLE static.label_dim (
    domain     TEXT     NOT NULL,  -- vocabulary name (the converted column family)
    code       SMALLINT NOT NULL,  -- the stored value in the owning table
    label      TEXT     NOT NULL,  -- the human/API string
    sort_order SMALLINT NOT NULL,  -- presentation order for API/UI listings

    CONSTRAINT pk_label_dim PRIMARY KEY (domain, code),
    CONSTRAINT uq_label_dim_domain_label UNIQUE (domain, label)
);

COMMENT ON TABLE  static.label_dim IS 'Closed-vocabulary label <-> SMALLINT code map (the label-code contract): every SMALLINT-coded label column stores the code and decodes through this table at the API boundary. Mirrors: _common/labels.py (Python), data_viz/api/services/label-codes.ts (TS) — temp_scripts/check_label_dim.py asserts the three stay identical. Labels never ride cudf compute frames as strings (cuDF 26.08 string host-transfer fault class). Seed rows: the database/sql/static/label_dim/10..60 purpose files.';
COMMENT ON COLUMN static.label_dim.domain     IS 'Vocabulary name = the owning column family: regime / side / action / weighting / rank_side / pool_size / px_speed / vol_state / board / sec_owner_type / contract_type / option_type / signal_type / sec_type (mov_ave family + analysis_signals) / ext_resource / ext_http_source / ext_llm_provider / db_instance / db_replication_state / analysis_cache_kind are CONVERTED (columns store the code); skew_type / attribution_type / trade_side are PENDING (vocabulary registered, columns still TEXT).';
COMMENT ON COLUMN static.label_dim.code       IS 'The value stored in the owning table''s converted column (0-based, SMALLINT).';
COMMENT ON COLUMN static.label_dim.label      IS 'The human/API string the code decodes to (calm, buy, HYPE, all, ...).';
COMMENT ON COLUMN static.label_dim.sort_order IS 'Presentation order for listings that enumerate a domain. pool_size deliberately differs from code order: it preserves the pre-refactor alphabetical TEXT order (all, large, mid, small) the API/UI already render; code order is semantic (small, mid, large, all).';
