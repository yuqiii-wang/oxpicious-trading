-- ============================================================================
--  static.keyword_maps seed — COMPONENT rows (category_type 4..7).
--
--  The grammatical modifier layer that combines with the market direction
--  verbs (上涨/下跌/攀升/…, seeded as market rows in 03 + statics/05) in
--  phrases like 大幅上涨, 高位震荡, 持续拉升. Four families:
--
--    4 adj       evaluative adjectives (强势, 疲弱) — pos=verb, the
--                established adjectives-are-predicates convention
--                (并不强势 negates like a verb);
--    5 adv       degree/manner adverbs (大幅, 快速) — pos=function, the
--                虚词 class (modify without referential content);
--    6 adv_noun  nouns functioning adverbially (高位, 盘中) — pos=noun;
--    7 adv_verb  verbs functioning adverbially (持续, 加速) — pos=verb.
--
--  Contract: like skip rows they carry NO target (both target columns
--  NULL) and are never extracted as audit keywords (builds.text.keywords
--  excludes them — a bare 大幅/持续 mention evidences no market state);
--  unlike skip they STAY in the token streams — content words the keyword
--  n-gram matcher (_common.tokenization.keyword_ngrams) composes with the
--  market verbs. Hand-authored INSERT SQL — THIS FILE is the vocabulary's
--  source of record: add / edit / remove entries here and re-apply (each
--  section re-applies by DELETE of its own category_type slice + INSERT;
--  run the whole file in ONE transaction).
-- ============================================================================

BEGIN;

-- adj (4) — evaluative adjectives
DELETE FROM static.keyword_maps WHERE category_type = 4;
INSERT INTO static.keyword_maps (keyword, category_type, industry_id, category_code, parts_of_speech, is_negation) VALUES
    ('强势', 4, NULL, NULL, 1, FALSE),
    ('疲弱', 4, NULL, NULL, 1, FALSE),
    ('强劲', 4, NULL, NULL, 1, FALSE),
    ('脆弱', 4, NULL, NULL, 1, FALSE),
    ('积极', 4, NULL, NULL, 1, FALSE),
    ('消极', 4, NULL, NULL, 1, FALSE),
    ('乐观', 4, NULL, NULL, 1, FALSE),
    ('悲观', 4, NULL, NULL, 1, FALSE),
    ('谨慎', 4, NULL, NULL, 1, FALSE),
    ('活跃', 4, NULL, NULL, 1, FALSE);

-- adv (5) — degree / manner adverbs
DELETE FROM static.keyword_maps WHERE category_type = 5;
INSERT INTO static.keyword_maps (keyword, category_type, industry_id, category_code, parts_of_speech, is_negation) VALUES
    ('大幅', 5, NULL, NULL, 3, FALSE),
    ('小幅', 5, NULL, NULL, 3, FALSE),
    ('微幅', 5, NULL, NULL, 3, FALSE),
    ('快速', 5, NULL, NULL, 3, FALSE),
    ('迅速', 5, NULL, NULL, 3, FALSE),
    ('急速', 5, NULL, NULL, 3, FALSE),
    ('显著', 5, NULL, NULL, 3, FALSE),
    ('明显', 5, NULL, NULL, 3, FALSE),
    ('温和', 5, NULL, NULL, 3, FALSE),
    ('略微', 5, NULL, NULL, 3, FALSE),
    ('大举', 5, NULL, NULL, 3, FALSE),
    ('全面', 5, NULL, NULL, 3, FALSE),
    ('稳步', 5, NULL, NULL, 3, FALSE),
    ('逐步', 5, NULL, NULL, 3, FALSE);

-- adv_noun (6) — nouns functioning adverbially (position / phase)
DELETE FROM static.keyword_maps WHERE category_type = 6;
INSERT INTO static.keyword_maps (keyword, category_type, industry_id, category_code, parts_of_speech, is_negation) VALUES
    ('高位', 6, NULL, NULL, 0, FALSE),
    ('低位', 6, NULL, NULL, 0, FALSE),
    ('盘中', 6, NULL, NULL, 0, FALSE),
    ('早盘', 6, NULL, NULL, 0, FALSE),
    ('尾盘', 6, NULL, NULL, 0, FALSE),
    ('底部', 6, NULL, NULL, 0, FALSE),
    ('顶部', 6, NULL, NULL, 0, FALSE);

-- adv_verb (7) — verbs functioning adverbially (aspect / manner)
DELETE FROM static.keyword_maps WHERE category_type = 7;
INSERT INTO static.keyword_maps (keyword, category_type, industry_id, category_code, parts_of_speech, is_negation) VALUES
    ('持续', 7, NULL, NULL, 1, FALSE),
    ('连续', 7, NULL, NULL, 1, FALSE),
    ('加速', 7, NULL, NULL, 1, FALSE),
    ('反复', 7, NULL, NULL, 1, FALSE),
    ('领涨', 7, NULL, NULL, 1, FALSE),
    ('领跌', 7, NULL, NULL, 1, FALSE);

COMMIT;
