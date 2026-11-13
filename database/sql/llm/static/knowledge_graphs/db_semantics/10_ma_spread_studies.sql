-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the rest of the
--  mov_ave_spread study family beyond the three tables seeded in 01/02 —
--  EMA spreads, OHLC gap anatomy, percentile bands and streaks, price×volume
--  states, trading-amount studies, RSI ranks/holiday, and the roof/floor
--  trio. Conventions: .agents/skills/kg-db-semantics. DDL:
--  kg_entities.sql + kg_edges.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent.
-- ============================================================================
--  2026-11-09: the 'table' entities migrated to the corpus rule
--  db_semantics_tables; since 2026-11-10 the payloads live in the
--  tables' own database/sql/ DDL files — ONE TABLE = ONE FILE, payload
--  in the leading banner (formerly the parallel database/kg_tables/
--  corpus, filename = the schema-qualified name). This seed keeps
--  only what doesn't fit a rule: the edge INSERTs and the DB
--  view entities. The rule's ledger owns the migrated tables;
--  edges here still resolve them BY NAME.


INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    ('analysis.mov_ave_spreads_detail_ema', 'stats.index_tech_stats', 'computed_from', 'for_ema'),
    ('analysis.mov_ave_spreads_detail_ema', 'stats.etf_tech_stats', 'computed_from', 'for_ema'),
    ('analysis.mov_ave_spreads_detail_ema', 'stats.stock_tech_stats', 'computed_from', 'for_ema'),
    ('analysis.mov_ave_spreads_detail_ema', 'stats.industry_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_detail_ema', 'analysis.mov_ave_spreads_detail', 'similar_to', 'about_ma_spread'),
    ('analysis.mov_ave_spreads_detail_trailing', 'analysis.mov_ave_spreads_detail', 'similar_to', 'about_ma_spread'),
    ('analysis.mov_ave_spreads_detail_ohlc_gaps', 'stats.etf_basic_stats', 'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_spreads_detail_ohlc_gaps', 'stats.stock_basic_stats', 'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_spreads_detail_ohlc_gaps', 'stats.etf_adjustment', 'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_spreads_codes', 'analysis.mov_ave_spreads_detail', 'computed_from', 'for_code_summary'),
    ('analysis.mov_ave_high_low_pct', 'stats.etf_basic_stats', 'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_high_low_pct', 'stats.stock_basic_stats', 'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_high_low_pct', 'stats.etf_adjustment', 'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_high_low_pct_streaks', 'analysis.mov_ave_high_low_pct', 'computed_from', 'for_band'),
    ('analysis.mov_ave_price_vs_amt', 'stats.etf_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_price_vs_amt', 'stats.stock_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_price_vs_amt', 'stats.etf_adjustment', 'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_price_vs_amt', 'stats.etf_liquidity_margin', 'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_price_vs_amt', 'stats.stock_liquidity_margin', 'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_trading_amt', 'stats.etf_liquidity_margin', 'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_trading_amt', 'stats.stock_liquidity_margin', 'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_trading_amt', 'stats.exchange_trading_amt', 'computed_from', 'for_market_share'),
    ('analysis.mov_ave_trading_amt_ratios', 'stats.etf_liquidity_margin', 'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_trading_amt_ratios', 'stats.stock_liquidity_margin', 'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_trading_amt_ratios', 'stats.etf_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_trading_amt_ratios', 'stats.stock_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_rsi_ranks', 'analysis.mov_ave_rsi', 'computed_from', 'for_rsi'),
    ('analysis.mov_ave_rsi_holiday', 'analysis.mov_ave_rsi', 'anchored_by', 'for_code_date'),
    ('analysis.mov_ave_rsi_holiday', 'stats.etf_basic_stats', 'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_rsi_holiday', 'stats.stock_basic_stats', 'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_spreads_roof_and_floor_lines', 'stats.etf_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_and_floor_lines', 'stats.stock_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_and_floor_lines', 'stats.etf_adjustment', 'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_spreads_roof_floor_estimates', 'analysis.mov_ave_spreads_roof_and_floor_lines', 'computed_from', 'for_trendline_method'),
    ('analysis.mov_ave_spreads_roof_floor_estimates', 'stats.etf_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_floor_estimates', 'stats.stock_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_floor_estimates', 'stats.etf_adjustment', 'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_spreads_roof_floor_rebounds', 'stats.etf_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_floor_rebounds', 'stats.stock_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_floor_rebounds', 'stats.etf_adjustment', 'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_spreads_roof_floor_estimates', 'analysis.mov_ave_spreads_roof_and_floor_lines', 'similar_to', 'about_roof_floor'),
    ('analysis.mov_ave_spreads_roof_floor_rebounds', 'analysis.mov_ave_spreads_roof_floor_estimates', 'similar_to', 'about_roof_floor'),
    ('analysis.mov_ave_spreads_detail_ohlc_gaps', 'stats.index_basic_stats', 'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_high_low_pct', 'stats.index_basic_stats', 'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_price_vs_amt', 'stats.index_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_price_vs_amt', 'stats.index_basic_stats', 'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_trading_amt', 'stats.index_basic_stats', 'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_trading_amt_ratios', 'stats.index_basic_stats', 'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_trading_amt_ratios', 'stats.index_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_rsi_holiday', 'stats.index_basic_stats', 'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_spreads_roof_and_floor_lines', 'stats.index_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_floor_estimates', 'stats.index_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_floor_rebounds', 'stats.index_basic_stats', 'computed_from', 'for_close')
) AS e(subject, object, relation, sub_relation)
JOIN llm.kg_entities a ON a.namespace = 0 AND a.name = e.subject
JOIN llm.kg_entities b ON b.namespace = 0 AND b.name = e.object
WHERE NOT EXISTS (
    SELECT 1 FROM llm.kg_edges x
    WHERE x.namespace = 0
      AND x.relation  = e.relation
      AND x.sub_relation IS NOT DISTINCT FROM e.sub_relation
      AND x.entity_ids = ARRAY[a.entity_id, b.entity_id]::BIGINT[]
);
