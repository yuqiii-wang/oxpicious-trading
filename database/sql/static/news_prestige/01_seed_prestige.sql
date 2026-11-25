-- ============================================================================
--  static.news_prestige seed — provider-level prestige rows.
--
--  One row per curated PROVIDER (author = '' — the provider-level grain;
--  exact (provider, author) overrides can be added here later, the PK
--  supports them). Covers the downloader pipelines (gov / ndrc /
--  pboc_oma / pboc_omo / pboc_lpr / zhihu / ai_daily) and the web-media
--  names the LLM-query reference downloads actually store (>=20 articles
--  in text.news as of 2026-11-22); everything else (the ~560 long-tail
--  one-article sites) stays unranked and orders after these by count.
--
--  rank — display order, lower first (official sources lead, the
--         catch-all `web` last); tier — text.news_providers news_type
--         vocabulary + `aggregator`; label — the chip tooltip.
--  THIS FILE is the prestige vocabulary's source of record: re-applies
--  by DELETE of everything + INSERT (run whole file in ONE transaction).
--  Never hand-UPDATE a row; edit here and re-apply.
-- ============================================================================

DELETE FROM static.news_prestige;

-- 1-6  official / pipeline sources
INSERT INTO static.news_prestige (provider, author, prestige) VALUES
('gov',        '', '{"rank": 1, "tier": "news_article",     "label": "政府官网"}'),
('ndrc',       '', '{"rank": 2, "tier": "news_article",     "label": "发改委"}'),
('pboc_oma',   '', '{"rank": 3, "tier": "financial_report", "label": "央行公告"}'),
('pboc_omo',   '', '{"rank": 4, "tier": "financial_report", "label": "央行OMO"}'),
('pboc_lpr',   '', '{"rank": 5, "tier": "financial_report", "label": "央行LPR"}'),
('ai_daily',   '', '{"rank": 6, "tier": "ai_digest",        "label": "AI日报"}');

-- 10  social UGC platform
INSERT INTO static.news_prestige (provider, author, prestige) VALUES
('zhihu',      '', '{"rank": 10, "tier": "social_media",    "label": "知乎"}');

-- 12-19  finance media
INSERT INTO static.news_prestige (provider, author, prestige) VALUES
('证券时报网',   '', '{"rank": 12, "tier": "news_article", "label": "证券时报"}'),
('第一财经',     '', '{"rank": 13, "tier": "news_article", "label": "第一财经"}'),
('新浪财经',     '', '{"rank": 14, "tier": "news_article", "label": "新浪财经"}'),
('东方财富网',   '', '{"rank": 15, "tier": "news_article", "label": "东方财富"}'),
('同花顺财经',   '', '{"rank": 16, "tier": "news_article", "label": "同花顺"}'),
('基金报官网',   '', '{"rank": 17, "tier": "news_article", "label": "中国基金报"}'),
('证券之星',     '', '{"rank": 18, "tier": "news_article", "label": "证券之星"}'),
('南方财富网',   '', '{"rank": 19, "tier": "news_article", "label": "南方财富网"}');

-- 20-27  general news portals
INSERT INTO static.news_prestige (provider, author, prestige) VALUES
('腾讯新闻',     '', '{"rank": 20, "tier": "news_article", "label": "腾讯新闻"}'),
('网易',       '', '{"rank": 21, "tier": "news_article", "label": "网易"}'),
('网易新闻',     '', '{"rank": 22, "tier": "news_article", "label": "网易新闻"}'),
('新浪新闻',     '', '{"rank": 23, "tier": "news_article", "label": "新浪新闻"}'),
('新浪网',     '', '{"rank": 24, "tier": "news_article", "label": "新浪网"}'),
('搜狐',       '', '{"rank": 25, "tier": "news_article", "label": "搜狐"}'),
('搜狐网',     '', '{"rank": 26, "tier": "news_article", "label": "搜狐网"}'),
('搜狐新闻',     '', '{"rank": 27, "tier": "news_article", "label": "搜狐新闻"}');

-- 30-34  social / community platforms
INSERT INTO static.news_prestige (provider, author, prestige) VALUES
('雪球',       '', '{"rank": 30, "tier": "social_media", "label": "雪球"}'),
('财富号',     '', '{"rank": 31, "tier": "social_media", "label": "财富号"}'),
('韭研公社',     '', '{"rank": 32, "tier": "social_media", "label": "韭研公社"}'),
('知乎专栏',     '', '{"rank": 33, "tier": "social_media", "label": "知乎专栏"}'),
('微信公众平台',  '', '{"rank": 34, "tier": "social_media", "label": "微信公众号"}');

-- 40+  aggregators / catch-all
INSERT INTO static.news_prestige (provider, author, prestige) VALUES
('163.com',    '', '{"rank": 40, "tier": "aggregator",   "label": "163.com"}'),
('web',        '', '{"rank": 90, "tier": "aggregator",   "label": "网络来源"}');
