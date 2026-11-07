-- ============================================================================
--  Knowledge-graph seed — namespace 1 (PROMPTS): the deep agent's prompt
--  catalog describes itself. One of the static/knowledge_graphs/ seed
--  files — DDL: 13_kg_hypergraph.sql; sibling scope: db_semantics/ (ns 0).
--
--  Namespace-1 conventions (what its records mean):
--    - ONE ENTITY = ONE CATALOGED PROMPT ROW of llm.ai_prompts
--      (prompt_type 1 skills + 2 plan — exactly the universe the
--      prompt-loading ladder's catalog serves; internal/tool/system
--      rows are NOT catalog entities: tool descriptions already ride
--      their tool schemas, internal rows are harness-own). name =
--      prompt_key (unique within the catalog), entity_type 'skill'
--      (type 1) | 'plan' (type 2), description = the row's PREVIEW
--      verbatim (the authored one-sentence business face — never
--      re-authored here), tags = the row's LABELS verbatim (bound ∪
--      optional — the authored bilingual findability vocabulary)
--      plus its prompt_sub_type, plus for unlabeled rows a few terms
--      taken from the preview/title text itself.
--    - EDGES = PROMPT RELATIONSHIPS, head-first (entity_ids[0] =
--      subject); relation = plain VERB, sub_relation = prepositional
--      phrase, the same shape contract as ns 0:
--      'similar_to'      two skills/plan rows teaching the SAME
--                        domain — sub_relation = about_<domain>.
--                        Grounded ONLY in authored structure: the same
--                        static/ai_prompts/ seed file family, parallel
--                        "how to read" previews, or labels naming the
--                        same concept. SYMMETRIC: seed one direction,
--                        walkers traverse either way.
--      'alternates_with' rows the picker offers as ONE mutually
--                        exclusive choice — sub_relation =
--                        about_study_scope (the four goal rows; the
--                        09_plan_prompts.sql header's own contract:
--                        "mutually exclusive scope directives, exactly
--                        one governing how wide a run studies").
--    - NO CROSS-NAMESPACE EDGES (the DDL contract: a hyperedge never
--      spans namespaces). Which TABLE a skill teaches is answered by
--      ns 0 (search it there); ns 1 answers WHICH SKILL teaches a
--      domain and how the catalog rows relate.
--
--  Grounding for every edge below (file families / preview facts):
--    ma-spread-guide ↔ roof-floor-guide: both teach the mov-ave-spread
--      study's structure (alignment/trend regime vs its roof/floor
--      levels — 05_commons, previews both about the MA-spread read).
--    pe-guide ↔ dividends-guide: the two "baseline" valuation reads
--      (05_commons; the ns-0 seed 11_pe_dividens groups the tables).
--    market-crowdedness-guide ↔ hot-stock-momentum-guide: both gauge
--      one-sided concentration (05_commons; ns-0 seed 13 groups the
--      crowdedness + hot-momentum tables).
--    rf-break-signal-guide / hedge-pairs-guide ↔ signals-families-guide:
--      all teach reading signal rule cells (06_signals; hedge-pairs is
--      one of the signal families).
--    futures-basis-guide ↔ options-guide: the two derivatives
--      positioning reads (07_derivatives). margin-trends-guide is
--      deliberately NOT edged to them: margin financing is borrowed
--      money in the CASH market (a leverage-flow lens), not the
--      derivatives positioning the basis/options guides teach —
--      similar only in the loose "leveraged" framing, which is not
--      the same domain.
--    db-search ↔ db-table-map: both teach where the data lives and
--      how to query it (02_db_query + 11_backend_tech). The
--      sandbox-study-kit row is deliberately NOT edged to them: it
--      teaches the sandbox RUNTIME kit, not DB access. Likewise
--      market-sentiment-guide is not edged to ai-qa-audit /
--      news-audit: it MINES the corpus for sentiment, the audits
--      teach corpus QUALITY procedure — a shared data source is not
--      a shared domain.
--    general-plan ↔ naive-verify-plan: the two orchestration plan-shape
--      rows (09's orchestration sub-family, multi-pick).
--    study-codes → study-codes-related → study-market-codes →
--    study-market-whole: the mutually exclusive goal radio (09),
--      chained in scope-widening order (symmetric traversal).
--
--  Idempotent — entities upsert in place on uq_kg_entities_ns_type_name
--  (entity_id / created_at untouched, so edge member ids stay stable);
--  edges insert only when the same (namespace, relation, sub_relation,
--  entity_ids) shape is absent. A row DEACTIVATED or re-keyed in
--  ai_prompts leaves its ns-1 entity behind until this seed is edited
--  (static seeds are the source of record; entity DELETE is a
--  temp_scripts one-time job, never parked here).
-- ============================================================================

INSERT INTO llm.kg_entities (namespace, name, entity_type, tags, description) VALUES
    -- prompt_type 1 — skills ------------------------------------------------
    (1, 'db-search', 'skill', ARRAY['db_query', 'db_keywords', 'schema', 'aggregate', '查库', '数据库'],
     'Ground every claim in the database — inspect schemas and run narrow aggregates with the metric db_keywords recipes before writing.'),
    (1, 'rarity-ranking', 'skill', ARRAY['db_query', 'percentile', 'rank', 'own history', '分位', '极值'],
     'Weigh every metric against its own trailing ~10y history and call out top/bottom-tail readings explicitly.'),
    (1, 'forked-evidence-search', 'skill', ARRAY['online_search', 'corpus lane', 'online lane', 'subagent fork', '外部证据', '联网检索'],
     'Fork every external-evidence question into parallel corpus-lane and online-lane subagents and attribute each fact to its lane.'),
    (1, 'cross-item-synthesis', 'skill', ARRAY['biz_guide', 'cross-item', 'synthesis', 'combined picture', '交叉引用', '综合'],
     'Cross-reference findings across the selected items and close with the combined market picture, not per-item summaries.'),
    (1, 'volume-price-guide', 'skill', ARRAY['biz_guide', '放量', '缩量', '量价', 'participation', 'volume', 'market share'],
     'The house 放量/缩量 volume vocabulary and the participation gauge (trading-amount market share vs its own MA).'),
    (1, 'ma-spread-guide', 'skill', ARRAY['biz_guide', '均线', '移动平均', 'MA20', 'moving average', 'ma alignment', 'ma spread', 'golden cross', 'death cross', 'trend regime'],
     'What the moving-average state means for trading — alignment, extension vs the security''s own history, crossing semantics and the trend regime.'),
    (1, 'roof-floor-guide', 'skill', ARRAY['biz_guide', '压力', '支撑', '顶底', 'roof floor levels', 'support resistance', 'donchian', 'level breach', 'consensus level'],
     'The roof/floor levels'' trading meaning — wick vs consensus levels and the measured breach semantics (anchoring support, capitulation V-bottom, controlled dip).'),
    (1, 'rf-break-signal-guide', 'skill', ARRAY['biz_guide', 'rf_break', '突破', '箱体', 'rf break', 'breakout signal', 'roof floor breakout', 'rule cells', 'turtle breakout'],
     'How to read the rf_break signal family''s rule cells — the depth-gated breakout buys, the all-buy side rule and the evidence columns.'),
    (1, 'signals-families-guide', 'skill', ARRAY['biz_guide', '信号', 'RSI', 'MACD', 'signal families', 'rule cells', 'rsi tails', 'macd crossover', 'bollinger band', 'rebound'],
     'How to read the standalone signal families'' rule cells — sides, adaptive bars, trend/regime splits and the measured evidence contract.'),
    (1, 'pe-guide', 'skill', ARRAY['biz_guide', 'PE', '市盈率', '估值', 'pe valuation', 'pe percentile', 'valuation streaks', 'cheap expensive'],
     'How to read the PE baseline — level, its own-history percentile and the persistence streaks.'),
    (1, 'dividends-guide', 'skill', ARRAY['biz_guide', '分红', '股息', '派息', 'dividends', 'dividend yield', 'payout estimate', 'delivery rate'],
     'How to read the dividend baseline — estimated vs delivered payouts and the delivery track record.'),
    (1, 'etf-holdings-guide', 'skill', ARRAY['biz_guide', 'ETF', '重仓股', '成分股', 'etf holdings', 'fund holdings', 'concentration', 'accumulation reduction'],
     'How to read the ETF holdings lens — the holdings mix, its concentration and the accumulation/reduction direction.'),
    (1, 'market-beta-guide', 'skill', ARRAY['biz_guide', 'beta', '贝塔', '联动', 'market beta', 'industry attribution', 'co-movement', 'etf contribution'],
     'How to read an industry''s market-beta attribution — its co-movement with the broad market vs its own idiosyncratic drift.'),
    (1, 'market-crowdedness-guide', 'skill', ARRAY['biz_guide', '拥挤', '过热', '抱团', 'market crowdedness', 'crowding', 'participation breadth', 'overheating'],
     'How to read the market-crowdedness lens — how one-sided market positioning has become.'),
    (1, 'hot-stock-momentum-guide', 'skill', ARRAY['biz_guide', '龙头', '连板', '热门', 'hot stock', 'speculative leaders', 'theme rotation', 'turnover energy'],
     'How to read the hot-stock momentum lens — where speculative trading energy concentrates and how it rotates.'),
    (1, 'margin-trends-guide', 'skill', ARRAY['biz_guide', '融资', '两融', '杠杆', 'margin financing', 'margin balance', 'leverage flow', 'exchange split'],
     'How to read margin financing — balance changes, index/industry participation and the exchange split.'),
    (1, 'futures-basis-guide', 'skill', ARRAY['biz_guide', '期货', '基差', '贴水', 'futures basis', 'premium discount', 'term structure', 'contract roll'],
     'How to read the futures basis — the spot-futures gap, its quintile rank and the term-structure posture.'),
    (1, 'options-guide', 'skill', ARRAY['biz_guide', '期权', '波动率', '偏度', 'options', 'open interest walls', 'implied volatility', 'vol risk premium', 'iv skew'],
     'How to read the options lens — OI walls, the IV/vol index, skew and the VRP posture.'),
    (1, 'opposite-industry-guide', 'skill', ARRAY['biz_guide', '负相关', '跷跷板', '行业对冲', 'opposite industry', 'negative correlation', 'industry pairs', 'hedge value'],
     'How to read the opposite-industry offsets — disjoint negatively-correlated industry pairs and their hedge use.'),
    (1, 'hedge-pairs-guide', 'skill', ARRAY['biz_guide', '对冲', '配对', 'hedge', 'hedge pairs', 'pair portfolio', 'rsi wash', 'long short spread', 'benefit audit'],
     'How to read the hedge-pairs portfolio signal — the trio wash (two offset legs + the broad market in RSI capitulation together).'),
    (1, 'market-sentiment-guide', 'skill', ARRAY['biz_guide', '情绪', '舆情', '新闻', 'market sentiment', 'text corpus', 'ai market qa', 'news digestion', 'sentiment mining'],
     'How to mine the house text corpus — AI market QA and sentiment news — for sentiment readings with dates and sources.'),
    (1, 'db-table-map', 'skill', ARRAY['backend_tech', 'db tables', 'table map', 'ohlcv views', 'study conventions', '表映射'],
     'What DB tables a study queries — the daily OHLCV views, the precomputed stats/analysis tables and their conventions.'),
    (1, 'sandbox-study-kit', 'skill', ARRAY['backend_tech', 'sandbox', 'study_kit', 'study calculations', 'daily bars', '沙箱'],
     'The finished study_kit in the sandbox — read-only queries, daily bars and the standard calculations, plus the session workspace layout.'),
    (1, 'ai-qa-audit', 'skill', ARRAY['text_corpus', '问答', 'AI回答', 'qa', 'ai qa', 'qa audit', 'qa sampling', 'answer audit', 'llm_qa', 'knowledge base'],
     'Audit the AI QA corpus: fix the dates of interest, sample a few answers, verify claims against the DB, fork a subagent on anything suspicious.'),
    (1, 'news-audit', 'skill', ARRAY['text_corpus', '新闻', '资讯', '舆情', 'news audit', 'news sampling', 'news comments', 'engagement', 'most commented', 'thumbs up', 'news corpus'],
     'Audit the news corpus: fix the dates of interest, sample the hottest articles (most votes/comments), verify against the data, fork a subagent on anything suspicious.'),
    -- prompt_type 2 — plan --------------------------------------------------
    (1, 'study-codes', 'plan', ARRAY['goal', 'scope', 'named codes', '指定代码', '范围'],
     'Default scope when the run names codes: study the named codes themselves only - their own readings, compared side by side; no market sweep, no related ring.'),
    (1, 'study-codes-related', 'plan', ARRAY['goal', 'scope', 'related ring', 'correlation', '关联代码'],
     'Study the named codes plus their related ring: negatively correlated codes and the broad market references, picked by measured return correlation and reported as evidence.'),
    (1, 'study-market-codes', 'plan', ARRAY['goal', 'scope', 'market sweep', 'shortlist', '全市场', '选股'],
     'Default scope when the run has no code tag: sweep the whole market, shortlist the codes that best answer the ask, deepen only the shortlist - the answer names the desired codes.'),
    (1, 'study-market-whole', 'plan', ARRAY['goal', 'scope', 'market reading', 'industry map', '市场全貌', '行业地图'],
     'Study the whole market as a whole: the market-level state across the items plus the noticeable industries the aggregate surfaces - a market reading and industry map, not a code pick list.'),
    (1, 'naive-verify-plan', 'plan', ARRAY['orchestration', 'verification', 're-check', '验证', '复核'],
     'Lay out naive verification steps and re-check every research claim and technical stat against the data before writing.'),
    (1, 'general-plan', 'plan', ARRAY['orchestration', 'thin plan', 'sub-plan', 'delegation', '主计划'],
     'Keep the main plan thin: scope entry + delegated sub-plan sections that name their study prompts + final synthesis — the granular planning happens inside each sub-plan node.')
ON CONFLICT (namespace, entity_type, name) DO UPDATE
    SET description = EXCLUDED.description,
        tags        = EXCLUDED.tags;

-- Edges resolve the entity ids from the upsert above (no FK can span
-- the array — member resolution is the writer's job, same namespace only).
INSERT INTO llm.kg_edges (namespace, relation, sub_relation, entity_ids)
SELECT 1, e.relation, e.sub_relation, ARRAY[a.entity_id, b.entity_id]::BIGINT[]
FROM (VALUES
    ('study-codes',               'study-codes-related',           'alternates_with', 'about_study_scope'),
    ('study-codes-related',       'study-market-codes',            'alternates_with', 'about_study_scope'),
    ('study-market-codes',        'study-market-whole',            'alternates_with', 'about_study_scope'),
    ('general-plan',              'naive-verify-plan',             'similar_to',      'about_plan_shape'),
    ('ma-spread-guide',           'roof-floor-guide',              'similar_to',      'about_ma_spread_levels'),
    ('pe-guide',                  'dividends-guide',               'similar_to',      'about_valuation_baseline'),
    ('market-crowdedness-guide',  'hot-stock-momentum-guide',      'similar_to',      'about_crowdedness'),
    ('rf-break-signal-guide',     'signals-families-guide',        'similar_to',      'about_signal_rule_cells'),
    ('hedge-pairs-guide',         'signals-families-guide',        'similar_to',      'about_signal_rule_cells'),
    ('futures-basis-guide',       'options-guide',                 'similar_to',      'about_derivatives'),
    ('db-search',                 'db-table-map',                  'similar_to',      'about_data_access')
) AS e(subject, object, relation, sub_relation)
JOIN llm.kg_entities a ON a.namespace = 1 AND a.name = e.subject
JOIN llm.kg_entities b ON b.namespace = 1 AND b.name = e.object
WHERE NOT EXISTS (
    SELECT 1 FROM llm.kg_edges x
    WHERE x.namespace = 1
      AND x.relation  = e.relation
      AND x.sub_relation IS NOT DISTINCT FROM e.sub_relation
      AND x.entity_ids = ARRAY[a.entity_id, b.entity_id]::BIGINT[]
);
