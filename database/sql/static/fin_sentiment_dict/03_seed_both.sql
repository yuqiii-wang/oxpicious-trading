-- ============================================================================
--  static.fin_sentiment_dict seed — BOTH (dual-sign) words (polarity=2).
--
--  CFSD 中文金融情感词典 (姜富伟 / 孟令超 / 唐国豪): 8,793 unique words —
--  5,600 negative (polarity=0) + 3,172 positive (1) + 21 dual-sign words
--  the source lists in BOTH sheets (polarity=2 'both', seeded in
--  03_seed_both.sql; the PK(word) table holds each word once, so the
--  duals live ONLY here). The raw xlsx carries 9,228 word cells with
--  in-sheet duplicates and one BOM-duplicated word; dedup happens here,
--  at seeding. Built by translating the Loughran-McDonald master
--  dictionary into Chinese and expanding it with word2vec over Chinese
--  financial corpora; validated on A-share return prediction (媒体文本
--  情绪与股票回报预测, 经济学(季刊) 2021年第4期). Free for research with
--  citation: Jiang, Lee, Martin & Zhou, "Manager Sentiment and Stock
--  Returns", Journal of Financial Economics 132(1) 2019, 126-149.
--
--  GENERATED from the source xlsx (github.com/MengLingchao/
--  Chinese_financial_sentiment_dictionary, downloaded 2026-11-21) by
--  temp_scripts/gen_cfsd_seeds.py — THIS FILE is the vocabulary's source
--  of record: the file re-applies by DELETE of the polarity=2 slice
--  + INSERT (run the whole file in ONE transaction). Never hand-UPDATE a
--  row; edit the owning seed file and re-apply.
-- ============================================================================

DELETE FROM static.fin_sentiment_dict WHERE polarity = 2;
INSERT INTO static.fin_sentiment_dict (word, polarity) VALUES
    ('不可思议的', 2),
    ('倔强', 2),
    ('入迷', 2),
    ('冷静', 2),
    ('出乎意料', 2),
    ('大胆', 2),
    ('安心', 2),
    ('安慰', 2),
    ('安然无恙', 2),
    ('心疼', 2),
    ('忘乎所以', 2),
    ('打破', 2),
    ('抢占', 2),
    ('挑三拣四', 2),
    ('揭发', 2),
    ('揭示', 2),
    ('检举', 2),
    ('洪流', 2),
    ('激进', 2),
    ('讨好', 2),
    ('较劲', 2);
COMMIT;
