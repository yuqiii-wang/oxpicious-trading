-- ============================================================================
--  static.keyword_maps seed — MARKET-DESCRIPTION rows (category_type=1).
--
--  (keyword, kw_market code): what the market / a price leg is doing —
--  下跌 → px_dn, 放量 → vol_up, … Hand-authored INSERT SQL — THIS FILE is
--  the vocabulary's source of record: add / edit / remove entries here and
--  re-apply (the file re-applies by DELETE of the category_type=1 slice +
--  INSERT; run the whole file in ONE transaction). The static.label_dim
--  'kw_market' domain (database/sql/static/label_dim/08_keyword_maps.sql,
--  mirrored in _common/labels.py KW_MARKET_CODE + the TS label-codes) must
--  carry every category_code used below.
--
--  Extracted as keywords only — a market row never sets
--  text.news.industry_id. 放量/缩量 follow the house participation-vs-own-MA
--  semantics (the ohlcv volume vocabulary), never absolute thresholds.
--  Keywords are 2+ chars on purpose: single-char 涨/跌 would substring-hit
--  inside every 上涨/大涨/涨幅 mention and double-count. Overlaps are fine —
--  consumers count each keyword independently (大幅上涨 counts as both
--  上涨 and 大幅上涨).
-- ============================================================================

BEGIN;
DELETE FROM static.keyword_maps WHERE category_type = 1;
INSERT INTO static.keyword_maps (keyword, category_type, industry_id, category_code, parts_of_speech, is_negation) VALUES
    -- px_up (0) — price rising
    ('上涨', 1, NULL, 0, 1, TRUE),
    ('上升', 1, NULL, 0, 1, TRUE),
    ('走高', 1, NULL, 0, 1, TRUE),
    ('走强', 1, NULL, 0, 1, TRUE),
    ('上行', 1, NULL, 0, 1, TRUE),
    ('攀升', 1, NULL, 0, 1, TRUE),
    ('涨幅', 1, NULL, 0, 0, TRUE),
    ('涨势', 1, NULL, 0, 0, TRUE),
    ('收涨', 1, NULL, 0, 1, TRUE),
    -- px_dn (1) — price falling
    ('下跌', 1, NULL, 1, 1, TRUE),
    ('下降', 1, NULL, 1, 1, TRUE),
    ('走低', 1, NULL, 1, 1, TRUE),
    ('走弱', 1, NULL, 1, 1, TRUE),
    ('下行', 1, NULL, 1, 1, TRUE),
    ('下挫', 1, NULL, 1, 1, TRUE),
    ('跌幅', 1, NULL, 1, 0, TRUE),
    ('跌势', 1, NULL, 1, 0, TRUE),
    ('收跌', 1, NULL, 1, 1, TRUE),
    ('回落', 1, NULL, 1, 2, TRUE),
    -- sharp_up (2) — sharp rise
    ('大涨', 1, NULL, 2, 1, TRUE),
    ('暴涨', 1, NULL, 2, 1, TRUE),
    ('飙升', 1, NULL, 2, 1, TRUE),
    ('急涨', 1, NULL, 2, 1, TRUE),
    ('猛涨', 1, NULL, 2, 1, TRUE),
    ('跳涨', 1, NULL, 2, 1, TRUE),
    ('大幅上涨', 1, NULL, 2, 1, TRUE),
    ('大幅上升', 1, NULL, 2, 1, TRUE),
    -- sharp_dn (3) — sharp fall
    ('大跌', 1, NULL, 3, 1, TRUE),
    ('暴跌', 1, NULL, 3, 1, TRUE),
    ('崩盘', 1, NULL, 3, 1, TRUE),
    ('急跌', 1, NULL, 3, 1, TRUE),
    ('猛跌', 1, NULL, 3, 1, TRUE),
    ('跳水', 1, NULL, 3, 1, TRUE),
    ('大幅下跌', 1, NULL, 3, 1, TRUE),
    ('大幅下降', 1, NULL, 3, 1, TRUE),
    -- gap_up (4) / gap_dn (5) — opening gaps
    ('高开', 1, NULL, 4, 2, TRUE),
    ('低开', 1, NULL, 5, 2, TRUE),
    -- limit_up (6) / limit_dn (7) — daily price limits
    ('涨停', 1, NULL, 6, 2, TRUE),
    ('涨停板', 1, NULL, 6, 0, TRUE),
    ('跌停', 1, NULL, 7, 2, TRUE),
    ('跌停板', 1, NULL, 7, 0, TRUE),
    -- rebound (8) — recovery after weakness
    ('反弹', 1, NULL, 8, 2, TRUE),
    ('回暖', 1, NULL, 8, 2, TRUE),
    ('回升', 1, NULL, 8, 2, TRUE),
    ('企稳', 1, NULL, 8, 2, TRUE),
    ('收复', 1, NULL, 8, 1, TRUE),
    ('止跌', 1, NULL, 8, 1, TRUE),
    -- pullback (9) — retracement / range
    ('回调', 1, NULL, 9, 2, TRUE),
    ('回撤', 1, NULL, 9, 2, TRUE),
    ('盘整', 1, NULL, 9, 2, TRUE),
    -- vol_up (10) — participation expansion (share of 成交额 vs own MA)
    ('放量', 1, NULL, 10, 1, TRUE),
    ('增量资金', 1, NULL, 10, 0, TRUE),
    ('成交放大', 1, NULL, 10, 2, TRUE),
    ('量能放大', 1, NULL, 10, 2, TRUE),
    ('天量', 1, NULL, 10, 0, TRUE),
    -- vol_dn (11) — participation contraction
    ('缩量', 1, NULL, 11, 1, TRUE),
    ('成交萎缩', 1, NULL, 11, 2, TRUE),
    ('量能萎缩', 1, NULL, 11, 2, TRUE),
    ('地量', 1, NULL, 11, 0, TRUE),
    -- divergence (12) — price move not confirmed by participation
    ('量价背离', 1, NULL, 12, 2, TRUE),
    ('背离', 1, NULL, 12, 2, TRUE),
    -- bullish (13) — positive tone / long side
    ('利好', 1, NULL, 13, 0, TRUE),
    ('看多', 1, NULL, 13, 1, TRUE),
    ('看涨', 1, NULL, 13, 1, TRUE),
    ('牛市', 1, NULL, 13, 0, TRUE),
    ('做多', 1, NULL, 13, 1, TRUE),
    ('多头', 1, NULL, 13, 0, TRUE),
    -- bearish (14) — negative tone / short side
    ('利空', 1, NULL, 14, 0, TRUE),
    ('看空', 1, NULL, 14, 1, TRUE),
    ('看跌', 1, NULL, 14, 1, TRUE),
    ('熊市', 1, NULL, 14, 0, TRUE),
    ('做空', 1, NULL, 14, 1, TRUE),
    ('空头', 1, NULL, 14, 0, TRUE),
    -- volatile (15) — range / volatility
    ('震荡', 1, NULL, 15, 2, TRUE),
    ('波动', 1, NULL, 15, 2, TRUE);
COMMIT;
