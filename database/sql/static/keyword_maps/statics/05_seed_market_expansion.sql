-- ============================================================================
--  static.keyword_maps seed — MARKET-DESCRIPTION EXPANSION (category_type=1).
--
--  Wave 2 (2026-11-01): keywords mined from the text.news corpus (28.4k
--  titles across gov/zhihu/ndrc/pboc/ai_daily + 40 AI Q&A texts — frequency
--  verified) and self-proposed to fill the vocabulary's real gaps. THIS
--  FILE owns exactly the keywords listed below; the bootstrap slice file
--  (../03_seed_market.sql) still owns the wave-1 rows. Apply AFTER 02..04
--  (03's slice DELETE would otherwise wipe these rows). Re-applies by
--  DELETE of its own keywords within the slice + INSERT — run the whole
--  file in ONE transaction.
--
--  New categories this wave (static.label_dim 'kw_market' 16..19, mirrored
--  in _common/labels.py KW_MARKET_CODE + the TS label-codes):
--    16 mood      — crowd feeling: 情绪, 恐慌, 避险, …
--    17 position  — crowd actions: 抄底, 加仓, 获利了结, 轮动, …
--    18 break_up  — technical level breaks UP: 突破, 站上, 创新高, …
--    19 break_dn  — technical level breaks DOWN: 跌破, 失守, 破净, …
--
--  Deliberately NOT added (mined but rejected by curation): 行情/走势
--  (fire on nearly every recap + quote-lookup post — would swamp the
--  audit), 复盘/收评/早盘/午盘/盘中/盘面 (session-journalism words, not
--  market-state descriptions), 成交额/成交量 (neutral metric mentions,
--  not expansion talk), 修复 (hits road/infrastructure repair in gov
--  bodies), 分化 (cross-sectional dispersion, not the 量价背离 sense).
-- ============================================================================

BEGIN;
DELETE FROM static.keyword_maps
WHERE category_type = 1
  AND keyword = ANY (ARRAY[
    '领涨', '拉升', '冲高', '高走', '强势', '普涨', '涨超', '上冲', '补涨',
    '领跌', '低走', '弱势', '普跌', '跌超', '承压', '阴跌', '杀跌', '下探', '补跌',
    '腰斩', '闪崩', '砸盘',
    '连板', '打板', '炸板',
    '回吐', '洗盘', '滞涨',
    '反包', '筑底', '见底', '探底', '抢反弹', '转暖',
    '主力资金', '北向资金', '南向资金', '融资余额', '换手率',
    '情绪', '恐慌', '乐观', '悲观', '避险', '风险偏好', '观望', '谨慎',
    '抄底', '追高', '获利了结', '割肉', '踏空', '清仓', '加仓', '减仓',
    '满仓', '空仓', '补仓', '轮动', '抱团',
    '突破', '站上', '创新高', '新高',
    '跌破', '失守', '告破', '跌穿', '创新低', '新低', '破净', '破发'
  ]);
INSERT INTO static.keyword_maps (keyword, category_type, industry_id, category_code, parts_of_speech, is_negation) VALUES
    -- px_up (0) — mined: 领涨(218) 拉升(123) 冲高(81) 高走(35) 普涨(58) 涨超(205) 补涨(11); proposed: 强势 上冲
    ('领涨', 1, NULL, 0, 1, TRUE),
    ('拉升', 1, NULL, 0, 1, TRUE),
    ('冲高', 1, NULL, 0, 1, TRUE),
    ('高走', 1, NULL, 0, 1, TRUE),
    ('强势', 1, NULL, 0, 1, TRUE),
    ('普涨', 1, NULL, 0, 1, TRUE),
    ('涨超', 1, NULL, 0, 1, TRUE),
    ('上冲', 1, NULL, 0, 1, TRUE),
    ('补涨', 1, NULL, 0, 1, TRUE),
    -- px_dn (1) — mined: 领跌(90) 低走(56) 普跌(63) 跌超(207) 承压(164) 杀跌(19) 补跌(1); proposed: 弱势 阴跌 下探
    ('领跌', 1, NULL, 1, 1, TRUE),
    ('低走', 1, NULL, 1, 1, TRUE),
    ('弱势', 1, NULL, 1, 1, TRUE),
    ('普跌', 1, NULL, 1, 1, TRUE),
    ('跌超', 1, NULL, 1, 1, TRUE),
    ('承压', 1, NULL, 1, 1, TRUE),
    ('阴跌', 1, NULL, 1, 1, TRUE),
    ('杀跌', 1, NULL, 1, 1, TRUE),
    ('下探', 1, NULL, 1, 1, TRUE),
    ('补跌', 1, NULL, 1, 1, TRUE),
    -- sharp_dn (3) — mined: 腰斩(25) 闪崩(4); proposed: 砸盘
    ('腰斩', 1, NULL, 3, 1, TRUE),
    ('闪崩', 1, NULL, 3, 1, TRUE),
    ('砸盘', 1, NULL, 3, 1, TRUE),
    -- limit_up (6) / limit_dn (7) — hot-stock momentum slang
    ('连板', 1, NULL, 6, 1, TRUE),
    ('打板', 1, NULL, 6, 1, TRUE),
    ('炸板', 1, NULL, 7, 1, TRUE),
    -- pullback (9) — mined: 回吐(22) 滞涨(4); proposed: 洗盘
    ('回吐', 1, NULL, 9, 1, TRUE),
    ('洗盘', 1, NULL, 9, 1, TRUE),
    ('滞涨', 1, NULL, 9, 1, TRUE),
    -- rebound (8) — mined: 探底(55) 筑底(21) 见底(17) 反包(10) 转暖(3); proposed: 抢反弹
    ('反包', 1, NULL, 8, 1, TRUE),
    ('筑底', 1, NULL, 8, 1, TRUE),
    ('见底', 1, NULL, 8, 1, TRUE),
    ('探底', 1, NULL, 8, 1, TRUE),
    ('抢反弹', 1, NULL, 8, 1, TRUE),
    ('转暖', 1, NULL, 8, 1, TRUE),
    -- vol_up (10) — flow words (mined: 主力资金(141) 北向资金(67)); who is
    -- participating / where the money moves
    ('主力资金', 1, NULL, 10, 0, TRUE),
    ('北向资金', 1, NULL, 10, 0, TRUE),
    ('南向资金', 1, NULL, 10, 0, TRUE),
    ('融资余额', 1, NULL, 10, 0, TRUE),
    ('换手率', 1, NULL, 10, 0, TRUE),
    -- mood (16) — crowd feeling (mined: 情绪(409) 避险(129) 恐慌(76)
    -- 风险偏好(59) 悲观(43) 乐观(41) 谨慎(28) 观望(19))
    ('情绪', 1, NULL, 16, 0, TRUE),
    ('恐慌', 1, NULL, 16, 0, TRUE),
    ('乐观', 1, NULL, 16, 1, TRUE),
    ('悲观', 1, NULL, 16, 1, TRUE),
    ('避险', 1, NULL, 16, 1, TRUE),
    ('风险偏好', 1, NULL, 16, 0, TRUE),
    ('观望', 1, NULL, 16, 1, TRUE),
    ('谨慎', 1, NULL, 16, 1, TRUE),
    -- position (17) — crowd actions: positioning / flows / crowding
    -- (mined: 轮动(55) 加仓(29) 抱团(27) 抄底(23) 获利了结(12) 减仓(13))
    ('抄底', 1, NULL, 17, 1, TRUE),
    ('追高', 1, NULL, 17, 1, TRUE),
    ('获利了结', 1, NULL, 17, 1, TRUE),
    ('割肉', 1, NULL, 17, 1, TRUE),
    ('踏空', 1, NULL, 17, 1, TRUE),
    ('清仓', 1, NULL, 17, 1, TRUE),
    ('加仓', 1, NULL, 17, 1, TRUE),
    ('减仓', 1, NULL, 17, 1, TRUE),
    ('满仓', 1, NULL, 17, 1, TRUE),
    ('空仓', 1, NULL, 17, 1, TRUE),
    ('补仓', 1, NULL, 17, 1, TRUE),
    ('轮动', 1, NULL, 17, 1, TRUE),
    ('抱团', 1, NULL, 17, 1, TRUE),
    -- break_up (18) — technical level breaks UP (mined: 新高(217)
    -- 突破(178) 创新高(51) 站上(23); 新高 matches inside 创新高/历史新高
    -- by design — the count is the milestone-talk breadth)
    ('突破', 1, NULL, 18, 1, TRUE),
    ('站上', 1, NULL, 18, 1, TRUE),
    ('创新高', 1, NULL, 18, 1, TRUE),
    ('新高', 1, NULL, 18, 0, TRUE),
    -- break_dn (19) — technical level breaks DOWN (mined: 失守(67)
    -- 新低(64) 跌破(51) 创新低(8) 破净(4) 跌穿(1) 破发(1); proposed: 告破)
    ('跌破', 1, NULL, 19, 1, TRUE),
    ('失守', 1, NULL, 19, 1, TRUE),
    ('告破', 1, NULL, 19, 1, TRUE),
    ('跌穿', 1, NULL, 19, 1, TRUE),
    ('创新低', 1, NULL, 19, 1, TRUE),
    ('新低', 1, NULL, 19, 0, TRUE),
    ('破净', 1, NULL, 19, 1, TRUE),
    ('破发', 1, NULL, 19, 1, TRUE);
COMMIT;
