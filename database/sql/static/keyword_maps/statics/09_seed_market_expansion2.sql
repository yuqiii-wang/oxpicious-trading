-- ============================================================================
--  static.keyword_maps seed — MARKET-DESCRIPTION EXPANSION 2 (category_type=1).
--
--  Wave 3 (2026-11-04): crowd-state / hot-money vocabulary mined from the
--  text.news corpus (28.6k titles, frequency + per-source verified —
--  temp_scripts/mine_keyword_wave3.py) in the spirit of 分歧: the 情绪周期
--  / 龙头战法 words the wave-1+2 slices underrepresent. THIS FILE owns
--  exactly the keywords listed below; the bootstrap slice (../03_seed_market.sql)
--  and wave 2 (05_seed_market_expansion.sql) still own their rows. Apply
--  AFTER 02..05 in filename order (05's slice DELETE only wipes its own
--  keywords, and so does this file's). Re-applies by DELETE of its own
--  keywords within the slice + INSERT — run the whole file in ONE transaction.
--
--  Deliberately NOT added (mined but rejected by curation):
--    一致 (7, gov:6)  — the corpus hits are 宏观政策取向一致性-style
--                       policy-consistency talk, not the 情绪周期 分歧→一致
--                       consensus sense; bare substring can't separate them.
--    高标 (40, gov:38 ndrc:2) — gov 高标准 swamp; the board-slang sense is
--                       unminable via substring.
--    启动 (54, gov:29) — program/clinical-trial launches swamp the market
--                       "行情启动" sense.
--    修复 (92, gov:27) — already rejected in wave 2 (road/infrastructure
--                       repair in gov bodies); re-confirmed.
--    派发 (1, gov)     — 减税红利派发, fiscal distribution not chips
--                       distribution (the market sense 出货 covers it).
--    接力 (7, gov:2)   — 接力赛 metaphor mixed in with 接力涨停.
--    发酵 (3)          — theme-brewing talk (涨价潮发酵, 地缘风险发酵),
--                       not a market-state description.
--    权重股/高位股/中军 — participant-structure nouns, no market state.
--    断板/首板/二板/三板/扫板/排板/锁仓/试盘/核按钮/反核/晋级/套牢/解套/
--    弱转强/强转弱/转强/转弱/追涨杀跌/高抛低吸/抛压/承接/胶着/存量博弈/
--    存量资金/轧空/高潮/暴涨暴跌 — ≤1 corpus hit each (三板's single hit is
--                       新三板 ambiguity): too rare to earn a row.
--    放量上涨/放量下跌/缩量上涨/缩量反弹 — volume×direction compounds have
--                       no single category; the factor keywords (放量/缩量 +
--                       上涨/下跌/反弹) already count independently.
-- ============================================================================

BEGIN;
DELETE FROM static.keyword_maps
WHERE category_type = 1
  AND keyword = ANY (ARRAY[
    '翻红', '转涨', '主升浪', '主升', '二波', '逼空',
    '翻绿', '转跌',
    '涨停潮', '地天板', '跌停潮', '天地板',
    '反转', '护盘', '站稳', '破位',
    '变盘', '拉锯',
    '分歧', '多空', '退潮', '冰点', '散户', '韭菜', '龙头', '龙头股', '妖股',
    '低吸', '抢筹', '出货', '追涨'
  ]);
INSERT INTO static.keyword_maps (keyword, category_type, industry_id, category_code, parts_of_speech, is_negation) VALUES
    -- px_up (0) — mined: 翻红(35) 二波(6) 主升(6) 主升浪(5) 转涨(3); 主升
    -- matches inside 主升浪 by design (the main-up-leg talk breadth)
    ('翻红', 1, NULL, 0, 1, TRUE),
    ('转涨', 1, NULL, 0, 1, TRUE),
    ('主升浪', 1, NULL, 0, 0, TRUE),
    ('主升', 1, NULL, 0, 0, TRUE),
    ('二波', 1, NULL, 0, 0, TRUE),
    -- sharp_up (2) — mined: 逼空(3); the squeeze-rally slang
    ('逼空', 1, NULL, 2, 1, TRUE),
    -- px_dn (1) — mined: 翻绿(6) 转跌(2)
    ('翻绿', 1, NULL, 1, 1, TRUE),
    ('转跌', 1, NULL, 1, 1, TRUE),
    -- limit_up (6) / limit_dn (7) — mined: 涨停潮(46) 跌停潮(2) 地天板(2)
    -- 天地板(2); the intraday limit-to-limit reversal slangs
    ('涨停潮', 1, NULL, 6, 0, TRUE),
    ('地天板', 1, NULL, 6, 0, TRUE),
    ('跌停潮', 1, NULL, 7, 0, TRUE),
    ('天地板', 1, NULL, 7, 0, TRUE),
    -- rebound (8) — mined: 反转(31) 护盘(8); direction-flip talk + support
    -- propping (银行护盘), the wave-1 反弹/企稳 family's flip side
    ('反转', 1, NULL, 8, 2, TRUE),
    ('护盘', 1, NULL, 8, 1, TRUE),
    -- volatile (15) — mined: 变盘(15) 拉锯(5); regime-change anticipation
    -- and the seesaw battle
    ('变盘', 1, NULL, 15, 1, TRUE),
    ('拉锯', 1, NULL, 15, 1, TRUE),
    -- break_up (18) / break_dn (19) — mined: 站稳(4) 破位(2); the 站上/
    -- 跌破 family's hold/broken-support forms
    ('站稳', 1, NULL, 18, 1, TRUE),
    ('破位', 1, NULL, 19, 1, TRUE),
    -- mood (16) — crowd state: opinion divergence/consensus, sentiment
    -- ebb/floor, and the crowd nouns (散户/韭菜/龙头/龙头股/妖股 — topic
    -- nouns, so NOT negation-flagged: a negated mention still names them)
    -- (mined: 龙头(154, gov only 3 — the 龙头企业 leakage is negligible)
    -- 韭菜(35) 散户(15) 分歧(11) 退潮(9) 妖股(7) 龙头股(27) 多空(5) 冰点(6))
    ('分歧', 1, NULL, 16, 0, TRUE),
    ('多空', 1, NULL, 16, 0, FALSE),
    ('退潮', 1, NULL, 16, 2, TRUE),
    ('冰点', 1, NULL, 16, 0, TRUE),
    ('散户', 1, NULL, 16, 0, FALSE),
    ('韭菜', 1, NULL, 16, 0, FALSE),
    ('龙头', 1, NULL, 16, 0, FALSE),
    ('龙头股', 1, NULL, 16, 0, FALSE),
    ('妖股', 1, NULL, 16, 0, FALSE),
    -- position (17) — crowd actions (mined: 出货(10) 抢筹(6) 低吸(5)
    -- 追涨(5); 追涨 parallels the seeded 追高)
    ('低吸', 1, NULL, 17, 1, TRUE),
    ('抢筹', 1, NULL, 17, 1, TRUE),
    ('出货', 1, NULL, 17, 1, TRUE),
    ('追涨', 1, NULL, 17, 1, TRUE);
COMMIT;
