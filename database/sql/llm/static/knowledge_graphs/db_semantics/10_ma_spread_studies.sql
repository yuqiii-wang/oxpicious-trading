-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the rest of the
--  mov_ave_spread study family beyond the three tables seeded in 01/02 —
--  EMA spreads, OHLC gap anatomy, percentile bands and streaks, price×volume
--  states, trading-amount studies, RSI ranks/holiday, and the roof/floor
--  trio. Conventions: .agents/skills/kg-db-semantics. DDL:
--  13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent.
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'analysis.mov_ave_spreads_detail_ema', 'table', ARRAY['ma-spread', '均线乖离', 'ema', 'indicators', '技术指标', 'macd'],
     'How far each security''s price and its short EMA sit from its longer EMAs, with slope and curvature — the MACD-style EMA gap set.'),
    (0, 'analysis.mov_ave_spreads_detail_ohlc_gaps', 'table', ARRAY['ohlc', 'K线', 'gap', '跳空', 'wick', '影线', 'intraday anatomy', '日内形态'],
     'Each security''s daily OHLC anatomy — overnight and intraday gaps, wicks, range and close position, plus recurring wick/gap patterns.'),
    (0, 'analysis.mov_ave_spreads_codes', 'table', ARRAY['ma-spread', '均线乖离', 'summary', '汇总', 'reference', '参考'],
     'Per-security summary of the MA-spread study — date coverage, all-time gap extremes and the latest snapshot.'),
    (0, 'analysis.mov_ave_high_low_pct',  'table', ARRAY['price band', '价格区间', 'percentile', '分位', 'support resistance', '支撑压力', 'high low', '高低点'],
     'Each security''s multi-period price percentile band — the high/low envelope of its trailing one-to-five-year range, one band per month.'),
    (0, 'analysis.mov_ave_high_low_pct_streaks', 'table', ARRAY['breakout', '突破', 'excursion', '越界', 'price band', '价格区间', 'streak', '连续'],
     'Excursion streaks of each security''s price outside its percentile band — breakout episodes with tolerated re-entries.'),
    (0, 'analysis.mov_ave_price_vs_amt',  'table', ARRAY['price volume', '量价', 'state', '状态', 'trading amount', '成交额', 'momentum', '动量'],
     'Each security-day''s price-speed × volume-state category — how the day''s move and trading amount sit versus their own trailing-year distributions.'),
    (0, 'analysis.mov_ave_trading_amt',   'table', ARRAY['trading amount', '成交额', 'liquidity', '流动性', 'market share', '成交占比', 'bollinger', '布林带'],
     'Each security''s trading-amount moving averages, band widths, market share and their slopes — the liquidity trend set.'),
    (0, 'analysis.mov_ave_trading_amt_ratios', 'table', ARRAY['liquidity impact', '流动性冲击', 'amihud', 'trading amount', '成交额'],
     'Each security''s liquidity-impact ratios — how much trading capital each unit of price movement cost (the Amihud measure inverted).'),
    (0, 'analysis.mov_ave_rsi_holiday',   'table', ARRAY['holiday', '假期', 'rsi', 'gap', '跳空', 'non-trading day', '非交易日'],
     'How each security trades around non-trading days — the pre-holiday context and the holiday-adjacent intraday gaps.'),
    (0, 'analysis.mov_ave_rsi_ranks',     'table', ARRAY['rsi', 'rank', '排名', 'momentum', '动量', 'history percentile', '历史分位'],
     'Each RSI reading''s rank within the security''s own full RSI history — where today''s momentum sits historically.'),
    (0, 'analysis.mov_ave_spreads_roof_and_floor_lines', 'table', ARRAY['roof floor', '顶底', 'anchor line', '锚线', 'support resistance', '支撑压力', 'high low', '高低点'],
     'Each security''s roof/floor anchor lines per lookback — the prior high/low anchors a rebound measures itself against.'),
    (0, 'analysis.mov_ave_spreads_roof_floor_estimates', 'table', ARRAY['roof floor', '顶底', 'resistance', '压力位', 'support', '支撑位', 'estimate', '估计'],
     'Quantitative resistance/support estimates per day and lookback — five methods (trendline, donchian, quantile, channel, bollinger) plus their consensus.'),
    (0, 'analysis.mov_ave_spreads_roof_floor_rebounds', 'table', ARRAY['roof floor', '顶底', 'rebound', '反弹', 'retest', '回踩', 'breakout', '突破'],
     'The rebound-and-retest audit of recorded roof/floor extremes — whether price rebounded off the level, retested it, held or broke it.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    -- EMA spreads read the tech_stats EMA columns (per its DDL comment);
    -- industry EMA is computed in-pipeline from industry closes
    ('analysis.mov_ave_spreads_detail_ema', 'stats.index_tech_stats',   'computed_from', 'for_ema'),
    ('analysis.mov_ave_spreads_detail_ema', 'stats.etf_tech_stats',     'computed_from', 'for_ema'),
    ('analysis.mov_ave_spreads_detail_ema', 'stats.stock_tech_stats',   'computed_from', 'for_ema'),
    ('analysis.mov_ave_spreads_detail_ema', 'stats.industry_basic_stats','computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_detail_ema', 'analysis.mov_ave_spreads_detail', 'similar_to', 'about_ma_spread'),
    -- OHLC gap anatomy from the raw bars (parent frame; ETF = adjusted close)
    ('analysis.mov_ave_spreads_detail_ohlc_gaps', 'stats.index_basic_stats', 'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_spreads_detail_ohlc_gaps', 'stats.etf_basic_stats',   'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_spreads_detail_ohlc_gaps', 'stats.stock_basic_stats', 'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_spreads_detail_ohlc_gaps', 'stats.etf_adjustment',    'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_spreads_codes', 'analysis.mov_ave_spreads_detail', 'computed_from', 'for_code_summary'),
    -- Percentile bands + streaks
    ('analysis.mov_ave_high_low_pct', 'stats.index_basic_stats', 'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_high_low_pct', 'stats.etf_basic_stats',   'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_high_low_pct', 'stats.stock_basic_stats', 'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_high_low_pct', 'stats.etf_adjustment',    'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_high_low_pct_streaks', 'analysis.mov_ave_high_low_pct', 'computed_from', 'for_band'),
    -- Price × volume states: price speed + amount level per sec_type
    ('analysis.mov_ave_price_vs_amt', 'stats.index_basic_stats',      'computed_from', 'for_close'),
    ('analysis.mov_ave_price_vs_amt', 'stats.etf_basic_stats',        'computed_from', 'for_close'),
    ('analysis.mov_ave_price_vs_amt', 'stats.stock_basic_stats',      'computed_from', 'for_close'),
    ('analysis.mov_ave_price_vs_amt', 'stats.etf_adjustment',         'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_price_vs_amt', 'stats.index_basic_stats',      'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_price_vs_amt', 'stats.etf_liquidity_margin',   'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_price_vs_amt', 'stats.stock_liquidity_margin', 'computed_from', 'for_trading_amount'),
    -- Trading-amount studies: amounts per sec_type + market share vs the
    -- exchange totals (per config/trading_amt.py)
    ('analysis.mov_ave_trading_amt', 'stats.index_basic_stats',      'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_trading_amt', 'stats.etf_liquidity_margin',   'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_trading_amt', 'stats.stock_liquidity_margin', 'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_trading_amt', 'stats.exchange_trading_amt',   'computed_from', 'for_market_share'),
    ('analysis.mov_ave_trading_amt_ratios', 'stats.index_basic_stats',      'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_trading_amt_ratios', 'stats.etf_liquidity_margin',   'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_trading_amt_ratios', 'stats.stock_liquidity_margin', 'computed_from', 'for_trading_amount'),
    ('analysis.mov_ave_trading_amt_ratios', 'stats.index_basic_stats',      'computed_from', 'for_close'),
    ('analysis.mov_ave_trading_amt_ratios', 'stats.etf_basic_stats',        'computed_from', 'for_close'),
    ('analysis.mov_ave_trading_amt_ratios', 'stats.stock_basic_stats',      'computed_from', 'for_close'),
    -- RSI ranks mirror the RSI table; holiday rows FK-CASCADE from it
    ('analysis.mov_ave_rsi_ranks',   'analysis.mov_ave_rsi',    'computed_from', 'for_rsi'),
    ('analysis.mov_ave_rsi_holiday', 'analysis.mov_ave_rsi',    'anchored_by',   'for_code_date'),
    ('analysis.mov_ave_rsi_holiday', 'stats.index_basic_stats', 'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_rsi_holiday', 'stats.etf_basic_stats',   'computed_from', 'for_ohlcv'),
    ('analysis.mov_ave_rsi_holiday', 'stats.stock_basic_stats', 'computed_from', 'for_ohlcv'),
    -- Roof/floor trio: anchors → estimates → rebound audit, on closes
    ('analysis.mov_ave_spreads_roof_and_floor_lines', 'stats.index_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_and_floor_lines', 'stats.etf_basic_stats',   'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_and_floor_lines', 'stats.stock_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_and_floor_lines', 'stats.etf_adjustment',    'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_spreads_roof_floor_estimates', 'analysis.mov_ave_spreads_roof_and_floor_lines', 'computed_from', 'for_trendline_method'),
    ('analysis.mov_ave_spreads_roof_floor_estimates', 'stats.index_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_floor_estimates', 'stats.etf_basic_stats',   'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_floor_estimates', 'stats.stock_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_floor_estimates', 'stats.etf_adjustment',    'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_spreads_roof_floor_rebounds',  'stats.index_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_floor_rebounds',  'stats.etf_basic_stats',   'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_floor_rebounds',  'stats.stock_basic_stats', 'computed_from', 'for_close'),
    ('analysis.mov_ave_spreads_roof_floor_rebounds',  'stats.etf_adjustment',    'computed_from', 'for_adjusted_close'),
    ('analysis.mov_ave_spreads_roof_floor_estimates', 'analysis.mov_ave_spreads_roof_and_floor_lines', 'similar_to', 'about_roof_floor'),
    ('analysis.mov_ave_spreads_roof_floor_rebounds',  'analysis.mov_ave_spreads_roof_floor_estimates', 'similar_to', 'about_roof_floor')
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
