-- ============================================================================
--  Knowledge-graph seed — namespace 0 (DB semantics): the debt / money-rate
--  chain (stats.debt_identity + the PBoC and market rate tables) and the
--  PBoC open-market announcements. Conventions:
--  .agents/skills/kg-db-semantics. DDL: 13_kg_hypergraph.sql.
--
--  Idempotent — entities upsert on uq_kg_entities_ns_type_name; edges
--  insert only when the (relation, sub_relation, entity_ids) triple is
--  absent. Also carries stats.v_debt_daily's edges (the view entity is
--  seeded in 03, its member entities here).
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    (0, 'stats.debt_identity',     'table', ARRAY['debt', '债券', 'rates', '利率', 'identity', '标识', 'reference', '参考'],
     'Which trading days carry debt-market data — the shared date identity for all debt rate and liquidity tables.'),
    (0, 'stats.debt_omo',          'table', ARRAY['debt', '债券', 'central bank', '央行', 'open market operations', '公开市场操作', 'reverse repo', '逆回购', 'liquidity', '流动性'],
     'The central bank''s open-market reverse-repo operations — the size, rate and maturity of each daily liquidity injection.'),
    (0, 'stats.debt_repo',         'table', ARRAY['debt', '债券', 'reverse repo', '逆回购', 'outstanding balance', '余额'],
     'The running stock of outstanding central-bank reverse repos — how much short-term liquidity is still deployed.'),
    (0, 'stats.debt_outright_repo','table', ARRAY['debt', '债券', 'central bank', '央行', 'bond buying', '买断式逆回购', 'liquidity', '流动性'],
     'The central bank''s outright repo purchases — the longer-tenor bond-buying liquidity injections.'),
    (0, 'stats.debt_mlf',          'table', ARRAY['debt', '债券', 'mlf', '中期借贷便利', 'central bank', '央行'],
     'The central bank''s medium-term lending facility tenders — the size and rate of each MLF injection.'),
    (0, 'stats.debt_shibor',       'table', ARRAY['debt', '债券', 'shibor', '拆借利率', 'interbank', '银行间', 'rates', '利率'],
     'The daily SHIBOR interbank offered rates across tenors.'),
    (0, 'stats.debt_treasury',     'table', ARRAY['debt', '债券', 'treasury', '国债', 'yield curve', '收益率曲线', 'rates', '利率'],
     'China treasury bond yields across maturities per day — the risk-free curve.'),
    (0, 'stats.debt_lpr',          'table', ARRAY['debt', '债券', 'lpr', '贷款市场报价利率', 'rates', '利率', 'central bank', '央行'],
     'The monthly loan prime rate announcement — the 1Y and 5Y+ lending benchmarks.'),
    (0, 'stats.pboc_oma',          'table', ARRAY['central bank', '央行', 'policy', '政策', 'announcement', '公告', 'liquidity', '流动性'],
     'The central bank''s open-market policy announcements — the high-level notices that set the liquidity stance.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 0, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    -- Debt sub-tables FK debt_identity on date
    ('stats.debt_omo',           'stats.debt_identity', 'anchored_by',   'for_date'),
    ('stats.debt_repo',          'stats.debt_identity', 'anchored_by',   'for_date'),
    ('stats.debt_outright_repo', 'stats.debt_identity', 'anchored_by',   'for_date'),
    ('stats.debt_mlf',           'stats.debt_identity', 'anchored_by',   'for_date'),
    ('stats.debt_shibor',        'stats.debt_identity', 'anchored_by',   'for_date'),
    ('stats.debt_treasury',      'stats.debt_identity', 'anchored_by',   'for_date'),
    ('stats.debt_lpr',           'stats.debt_identity', 'anchored_by',   'for_date'),
    -- v_debt_daily (entity seeded in 03) joins the debt chain per date
    ('stats.v_debt_daily', 'stats.debt_identity', 'computed_from', 'for_identity'),
    ('stats.v_debt_daily', 'stats.debt_treasury', 'computed_from', 'for_treasury_yield'),
    ('stats.v_debt_daily', 'stats.debt_shibor',   'computed_from', 'for_shibor'),
    ('stats.v_debt_daily', 'stats.debt_omo',      'computed_from', 'for_policy_ops')
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
