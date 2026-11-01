-- ============================================================================
--  static.label_dim seed — KEYWORD MAP vocabularies
--
--  The categorical columns of static.keyword_maps
--  (database/sql/static/keyword_maps/01_keyword_maps.sql): which family a
--  keyword row's category belongs to, and — per family — the category code
--  itself. Owning table: static.keyword_maps only.
-- ============================================================================

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- kw_category_type: the keyword-map row's category family. Market /
    -- broadmarket keywords are extracted as keywords but never set
    -- text.news.industry_id; industry rows carry the canonical
    -- sec_classification industry_id. skip (3) rows are the low-semantic
    -- function-word vocabulary (其实/也/就是说; the/a/of — en rows carry
    -- language=1) the search-index tokenization post-process drops
    -- (_common.tokenization) — never extracted as keywords, both target
    -- columns NULL. 4..7 (adj / adv / adv_noun / adv_verb) are the
    -- grammatical modifier component families (大幅/强势/高位/持续) that
    -- compose with the market direction verbs — kept in the token
    -- streams, never extracted, both target columns NULL.
    ('kw_category_type', 0, 'industry',    0),
    ('kw_category_type', 1, 'market',      1),
    ('kw_category_type', 2, 'broadmarket', 2),
    ('kw_category_type', 3, 'skip',        3),
    ('kw_category_type', 4, 'adj',         4),
    ('kw_category_type', 5, 'adv',         5),
    ('kw_category_type', 6, 'adv_noun',    6),
    ('kw_category_type', 7, 'adv_verb',    7),
    -- kw_market: the market-state description a keyword evidences
    -- (keyword_maps.category_code when category_type=market).
    -- Family-grouped code order: price direction, extremes, gaps, limits,
    -- recovery/retracement, participation, divergence, sentiment, crowd
    -- mood + positioning, technical level breaks.
    -- 放量/缩量 carry the house participation-vs-own-MA semantics.
    ('kw_market',  0,  'px_up',      0),
    ('kw_market',  1,  'px_dn',      1),
    ('kw_market',  2,  'sharp_up',   2),
    ('kw_market',  3,  'sharp_dn',   3),
    ('kw_market',  4,  'gap_up',     4),
    ('kw_market',  5,  'gap_dn',     5),
    ('kw_market',  6,  'limit_up',   6),
    ('kw_market',  7,  'limit_dn',   7),
    ('kw_market',  8,  'rebound',    8),
    ('kw_market',  9,  'pullback',   9),
    ('kw_market', 10,  'vol_up',    10),
    ('kw_market', 11,  'vol_dn',    11),
    ('kw_market', 12,  'divergence',12),
    ('kw_market', 13,  'bullish',   13),
    ('kw_market', 14,  'bearish',   14),
    ('kw_market', 15,  'volatile',  15),
    -- crowd mood / positioning / technical level breaks (2026-11-01 wave 2)
    ('kw_market', 16,  'mood',      16),
    ('kw_market', 17,  'position',  17),
    ('kw_market', 18,  'break_up',  18),
    ('kw_market', 19,  'break_dn',  19),
    -- kw_pos: the keyword's dominant lexical class
    -- (keyword_maps.parts_of_speech). verb = predicates incl. adjectives
    -- (并不强势 negates like a verb); noun_verb = dual-class words
    -- (回调 as "a pullback" / "to pull back"); noun = things;
    -- function = 虚词 (adverbs / conjunctions / particles / discourse
    -- markers — the skip rows' class and the adv component rows' class:
    -- 大幅/快速 modify without referential content).
    ('kw_pos', 0, 'noun',      0),
    ('kw_pos', 1, 'verb',      1),
    ('kw_pos', 2, 'noun_verb', 2),
    ('kw_pos', 3, 'function',  3),
    -- kw_language: the keyword's language (keyword_maps.language). The
    -- seed files default to zh; the English rows in
    -- keyword_maps/statics/08_seed_english.sql carry en explicitly.
    ('kw_language', 0, 'zh', 0),
    ('kw_language', 1, 'en', 1),
    -- kw_theme: the macro-policy theme (keyword_maps.category_code when
    -- category_type=broadmarket) — themes have no catalog industry.
    -- Order = the former downloads/macro/gov/keywords.json broadmarket
    -- key order.
    ('kw_theme',   0,  'economy',             0),
    ('kw_theme',   1,  'monetary',            1),
    ('kw_theme',   2,  'fiscal',              2),
    ('kw_theme',   3,  'trade',               3),
    ('kw_theme',   4,  'reform',              4),
    ('kw_theme',   5,  'employment',          5),
    ('kw_theme',   6,  'environment',         6),
    ('kw_theme',   7,  'demographic',         7),
    ('kw_theme',   8,  'investment',          8),
    ('kw_theme',   9,  'consumption_macro',   9),
    ('kw_theme',  10,  'prices',             10),
    ('kw_theme',  11,  'industrial_policy',  11),
    ('kw_theme',  12,  'financial_stability',12)
ON CONFLICT (domain, code) DO NOTHING;
