-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the PE and dividend
--  studies — the unified PE series, its monthly percentile bands and band
--  breaks, the trailing dividend yield, the cum-dividend fair-value curve,
--  the delivery audit, and the stock dividend history beneath them.
--  Conventions: .agents/skills/kg-db-semantics. DDL: 13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent.
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'analysis.pe',                  'table', ARRAY['pe', '市盈率', 'valuation', '估值'],
     'Each security''s daily price-to-earnings ratio, unified across index, ETF, stock and composite-industry sources.'),
    (0, 'analysis.pe_codes',            'table', ARRAY['pe', '市盈率', 'dividend yield', '股息率', 'summary', '汇总', 'reference', '参考'],
     'Per-security summary of the PE and dividend-yield studies — date coverage, latest readings and names.'),
    (0, 'analysis.pe_pct',              'table', ARRAY['pe', '市盈率', 'percentile band', '分位区间', 'valuation', '估值'],
     'Each security''s monthly PE percentile band over trailing one-to-five-year windows — the cheap/expensive envelope of its own history.'),
    (0, 'analysis.pe_pct_streaks',      'table', ARRAY['pe', '市盈率', 'band break', '破区间', 'episode', '区间段', 'valuation', '估值'],
     'Stretches when each security''s PE broke out of its monthly band — under/over-valuation episodes with tolerated re-entries.'),
    (0, 'analysis.dividends',           'table', ARRAY['dividend yield', '股息率', 'dividend', '分红'],
     'Each security''s trailing-twelve-month dividend yield per day.'),
    (0, 'analysis.dividend_est',        'table', ARRAY['dividend', '分红', 'fair value', '公允价值', 'ex-dividend', '除息', 'etf'],
     'Each payer''s estimated cum-dividend fair-value price curve — the ex-dividend sawtooth smoothed into a growth line (ETFs for now).'),
    (0, 'analysis.dividend_deliveries', 'table', ARRAY['dividend', '分红', 'delivery', '兑付', 'stability', '稳定性', 'audit', '审计'],
     'Each payer''s rolling five-year dividend delivery audit — yield, per-share growth and the year-over-year stability of its payout dates.'),
    (0, 'stats.stock_dividends',        'table', ARRAY['stock', '股票', 'dividend', '分红', 'ex-dividend date', '除息日', 'reference', '参考'],
     'Each stock''s dividend history — per-share amounts and ex-dividend dates.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    -- Unified PE sources per sec_type (per its DDL comment)
    ('analysis.pe', 'stats.index_valuation',     'computed_from', 'for_pe'),
    ('analysis.pe', 'stats.etf_basic_stats',     'computed_from', 'for_pe'),
    ('analysis.pe', 'stats.stock_basic_stats',   'computed_from', 'for_pe'),
    ('analysis.pe', 'stats.industry_basic_stats','computed_from', 'for_pe'),
    -- PE band + streak chain
    ('analysis.pe_pct',         'analysis.pe',         'computed_from', 'for_pe'),
    ('analysis.pe_pct_streaks', 'analysis.pe_pct',     'computed_from', 'for_band'),
    ('analysis.pe_pct_streaks', 'analysis.pe',         'computed_from', 'for_pe'),
    ('analysis.pe_codes',       'analysis.pe',         'computed_from', 'for_pe'),
    ('analysis.pe_codes',       'analysis.dividends',  'computed_from', 'for_dividend_yield'),
    -- Trailing dividend yield: per-share dividends, composition weights for
    -- the index cap-weighting, closes for the denominator
    ('analysis.dividends', 'stats.stock_dividends',    'computed_from', 'for_dividend'),
    ('analysis.dividends', 'stats.etf_adjustment',     'computed_from', 'for_dividend'),
    ('analysis.dividends', 'stats.sec_composition',    'computed_from', 'for_member_weights'),
    ('analysis.dividends', 'stats.index_basic_stats',  'computed_from', 'for_close'),
    ('analysis.dividends', 'stats.etf_basic_stats',    'computed_from', 'for_close'),
    ('analysis.dividends', 'stats.stock_basic_stats',  'computed_from', 'for_close'),
    -- Cum-dividend fair-value curve (ETF scope: ex-dates + closes)
    ('analysis.dividend_est', 'stats.etf_adjustment',  'computed_from', 'for_dividend'),
    ('analysis.dividend_est', 'stats.etf_basic_stats', 'computed_from', 'for_close'),
    -- Delivery audit: yields + per-share events + eps at ex-dates
    ('analysis.dividend_deliveries', 'analysis.dividends',     'computed_from', 'for_dividend_yield'),
    ('analysis.dividend_deliveries', 'stats.stock_dividends',  'computed_from', 'for_dividend'),
    ('analysis.dividend_deliveries', 'stats.stock_basic_stats','computed_from', 'for_eps')
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
