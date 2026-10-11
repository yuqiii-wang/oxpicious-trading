-- ============================================================================
--  Table: stats.label_dim  (closed-vocabulary label <-> SMALLINT code map)
--
--  The DB-side source of truth for every SMALLINT-coded label column
--  (the label-code contract): a converted column stores the CODE, this
--  table maps code -> label, and the boundary mirrors carry the same
--  mapping — Python _common/labels.py (pipelines), TypeScript
--  data_viz/api/services/label-codes.ts (API decode/params).
--  temp_scripts/check_label_dim.py asserts all three stay identical.
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

DROP TABLE IF EXISTS stats.label_dim CASCADE;

CREATE TABLE stats.label_dim (
    domain     TEXT     NOT NULL,  -- vocabulary name (the converted column family)
    code       SMALLINT NOT NULL,  -- the stored value in the owning table
    label      TEXT     NOT NULL,  -- the human/API string
    sort_order SMALLINT NOT NULL,  -- presentation order for API/UI listings

    CONSTRAINT pk_label_dim PRIMARY KEY (domain, code),
    CONSTRAINT uq_label_dim_domain_label UNIQUE (domain, label)
);

INSERT INTO stats.label_dim (domain, code, label, sort_order) VALUES
    -- regime: code order == builds.market_regimes REGIMES tuple ==
    -- lexicographic label order (ORDER BY code == ORDER BY label)
    ('regime',     0, 'calm',   0),
    ('regime',     1, 'hot',    1),
    ('regime',     2, 'panic',  2),
    ('regime',     3, 'quiet',  3),
    -- 4 = the POOLED population (no regime split) — signal_curves only:
    -- the per-key curve fitted over ALL the cell's triggers regardless
    -- of regime (not a market state; never emitted by the tiers that
    -- record a day's actual regime)
    ('regime',     4, 'all',    4),
    -- side: the bucket's own side of the indicator
    ('side',       0, 'top',    0),
    ('side',       1, 'bottom', 1),
    -- action: 0 buy (reversal up), 1 sell (down) — pairs with the
    -- excess-sign convention (sell <-> positive signal_excess)
    ('action',     0, 'buy',    0),
    ('action',     1, 'sell',   1),
    -- weighting: industry aggregation family (equal-weight vs
    -- trading_amount-weighted)
    ('weighting',  0, 'equal',  0),
    ('weighting',  1, 'amt',    1),
    -- rank_side: hypes-and-drains ranking direction
    ('rank_side',  0, 'HYPE',   0),
    ('rank_side',  1, 'DRAIN',  1),
    -- pool_size: code order is SEMANTIC (small -> mid -> large -> all);
    -- sort_order keeps the pre-refactor TEXT presentation order
    -- (alphabetical) so API/UI listings are unchanged
    ('pool_size',  0, 'small',  3),
    ('pool_size',  1, 'mid',    2),
    ('pool_size',  2, 'large',  1),
    ('pool_size',  3, 'all',    0),
    -- side (extended): flat is the price-vs-amt registry's mid band only
    ('side',       2, 'flat',   2),
    -- px_speed: price-vs-amt price leg, signed semantic order
    ('px_speed',   0, 'sharp_dn', 0),
    ('px_speed',   1, 'slow_dn',  1),
    ('px_speed',   2, 'flat',     2),
    ('px_speed',   3, 'slow_up',  3),
    ('px_speed',   4, 'sharp_up', 4),
    -- vol_state: price-vs-amt volume leg
    ('vol_state',  0, 'shrink', 0),
    ('vol_state',  1, 'normal', 1),
    ('vol_state',  2, 'heavy',  2),
    -- board: listing board
    ('board',      0, 'MAIN', 0),
    ('board',      1, 'STAR', 1),
    ('board',      2, 'GEM',  2),
    ('board',      3, 'BSE',  3),
    -- sec_owner_type: stats.sec_owners.type vocabulary (CHECK order)
    ('sec_owner_type', 0, 'fund_manager', 0),
    ('sec_owner_type', 1, 'broker',       1),
    ('sec_owner_type', 2, 'asset_manager', 2),
    ('sec_owner_type', 3, 'insurance',    3),
    ('sec_owner_type', 4, 'bank',         4),
    ('sec_owner_type', 5, 'company',      5),
    ('sec_owner_type', 6, 'other',        6),
    -- option_type: options contract class (converted 2026-10-07 —
    -- stats.options_terms + the analysis.options_* tables)
    ('option_type', 0, 'CALL', 0),
    ('option_type', 1, 'PUT',  1),
    -- PENDING domains below: vocabulary registered ahead of the table
    -- conversion — the owning columns are STILL TEXT (see header)
    -- skew_type: options skew metric family (DDL order)
    ('skew_type',  0, 'oi_moneyness', 0),
    ('skew_type',  1, 'iv_smile',     1),
    ('skew_type',  2, 'greek_delta',  2),
    ('skew_type',  3, 'greek_gamma',  3),
    ('skew_type',  4, 'greek_vega',   4),
    -- contract_type: futures
    ('contract_type', 0, 'index', 0),
    ('contract_type', 1, 'bond',  1),
    -- signal_type: the analysis_signals detection family (converted
    -- 2026-10-10; registry order = pipeline order)
    ('signal_type', 0, 'mov_rsi',  0),
    ('signal_type', 1, 'bollinger', 1),
    -- sec_type: security class (converted in the analysis.mov_ave*
    -- family + the analysis_signals tables — other sec_type columns
    -- keep TEXT, see header)
    ('sec_type',   0, 'etf',   0),
    ('sec_type',   1, 'index', 1),
    ('sec_type',   2, 'stock', 2),
    -- attribution_type: industry attributions (PENDING)
    ('attribution_type', 0, 'equal',      0),
    ('attribution_type', 1, 'trading_amt', 1),
    -- trade_side: strategy.trade_decision (PENDING)
    ('trade_side', 0, 'BUY',  0),
    ('trade_side', 1, 'SELL', 1),
    -- ext_resource: monitor.external_resource_audit.resource_type — the
    -- external resource family (0 downloads HTTP proxy, 1 llm providers)
    ('ext_resource',     0, 'http', 0),
    ('ext_resource',     1, 'llm',  1),
    -- ext_http_source: sub_type when resource_type = 0 — the request
    -- host family (suffix-matched from the URL by
    -- _common/external_resource_audit.py); 99 = unrecognized host
    ('ext_http_source',  0, 'sse',        0),
    ('ext_http_source',  1, 'szse',       1),
    ('ext_http_source',  2, 'bse',        2),
    ('ext_http_source',  3, 'cffex',      3),
    ('ext_http_source',  4, 'csindex',    4),
    ('ext_http_source',  5, 'cnindex',    5),
    ('ext_http_source',  6, 'chinabond',  6),
    ('ext_http_source',  7, 'shibor',     7),
    ('ext_http_source',  8, 'huatai',     8),
    ('ext_http_source',  9, 'eastmoney',  9),
    ('ext_http_source', 10, 'cninfo',    10),
    ('ext_http_source', 11, 'gov',       11),
    ('ext_http_source', 12, 'pboc',      12),
    ('ext_http_source', 13, 'zhihu',     13),
    ('ext_http_source', 99, 'other',     99),
    -- ext_llm_provider: sub_type when resource_type = 1 — the provider
    -- name (RetryingJsonClient's owning BaseLlmProvider.name); 99 fallback
    ('ext_llm_provider',  0, 'zhipu',  0),
    ('ext_llm_provider', 99, 'other', 99),
    -- db_instance: monitor.db_health.instance — the Postgres instance a
    -- health sample came from (docker-compose: db + db-replica)
    ('db_instance', 0, 'primary', 0),
    ('db_instance', 1, 'replica', 1),
    -- db_replication_state: monitor.db_health.replication_state — the WAL
    -- shipping state AS SEEN FROM THE SAMPLED INSTANCE (primary: the
    -- standby's pg_stat_replication.state; replica:
    -- pg_stat_wal_receiver.status). Code order is semantic: streaming is
    -- the only healthy state; 99 none = no standby attached (primary) /
    -- receiver absent (replica)
    ('db_replication_state',  0, 'streaming',  0),
    ('db_replication_state',  1, 'catchup',    1),
    ('db_replication_state',  2, 'startup',    2),
    ('db_replication_state',  3, 'backup',     3),
    ('db_replication_state',  4, 'stopping',   4),
    ('db_replication_state',  5, 'connecting', 5),
    ('db_replication_state',  6, 'stopped',    6),
    ('db_replication_state', 99, 'none',      99);

COMMENT ON TABLE  stats.label_dim IS 'Closed-vocabulary label <-> SMALLINT code map (the label-code contract): every SMALLINT-coded label column stores the code and decodes through this table at the API boundary. Mirrors: _common/labels.py (Python), data_viz/api/services/label-codes.ts (TS) — temp_scripts/check_label_dim.py asserts the three stay identical. Labels never ride cudf compute frames as strings (cuDF 26.08 string host-transfer fault class).';
COMMENT ON COLUMN stats.label_dim.domain     IS 'Vocabulary name = the owning column family: regime / side / action / weighting / rank_side / pool_size / px_speed / vol_state / board / sec_owner_type / contract_type / option_type / signal_type / sec_type (mov_ave family + analysis_signals) / ext_resource / ext_http_source / ext_llm_provider / db_instance / db_replication_state are CONVERTED (columns store the code); skew_type / attribution_type / trade_side are PENDING (vocabulary registered, columns still TEXT).';
COMMENT ON COLUMN stats.label_dim.code       IS 'The value stored in the owning table''s converted column (0-based, SMALLINT).';
COMMENT ON COLUMN stats.label_dim.label      IS 'The human/API string the code decodes to (calm, buy, HYPE, all, ...).';
COMMENT ON COLUMN stats.label_dim.sort_order IS 'Presentation order for listings that enumerate a domain. pool_size deliberately differs from code order: it preserves the pre-refactor alphabetical TEXT order (all, large, mid, small) the API/UI already render; code order is semantic (small, mid, large, all).';
