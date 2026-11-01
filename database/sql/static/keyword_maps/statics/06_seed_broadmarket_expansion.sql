-- ============================================================================
--  static.keyword_maps seed — BROADMARKET EXPANSION (category_type=2).
--
--  Wave 2 (2026-11-01): policy-theme keywords mined from the gov/pboc
--  corpus + current policy vocabulary. THIS FILE owns exactly the keywords
--  listed below; the bootstrap slice file (../04_seed_broadmarket.sql)
--  still owns the wave-1 rows. Apply AFTER 02..04 (04's slice DELETE would
--  otherwise wipe these rows). Re-applies by DELETE of its own keywords
--  within the slice + INSERT — run the whole file in ONE transaction.
-- ============================================================================

BEGIN;
DELETE FROM static.keyword_maps
WHERE category_type = 2
  AND keyword = ANY (ARRAY[
    '化债', '出口管制', '稳就业', '反内卷', '新质生产力', '设备更新'
  ]);
INSERT INTO static.keyword_maps (keyword, category_type, industry_id, category_code, parts_of_speech, is_negation) VALUES
    -- fiscal (2): the 2024→ local-government debt-resolution program
    ('化债', 2, NULL, 2, 1, TRUE),
    -- trade (3): export controls — the recurring trade-friction lever
    ('出口管制', 2, NULL, 3, 0, FALSE),
    -- employment (5): the employment-stability policy phrase
    ('稳就业', 2, NULL, 5, 1, TRUE),
    -- industrial_policy (11): anti-involution supply discipline + new
    -- productive forces — the current industrial-policy vocabulary
    ('反内卷', 2, NULL, 11, 1, TRUE),
    ('新质生产力', 2, NULL, 11, 0, FALSE),
    -- investment (8): the large-scale equipment renewal program (两新)
    ('设备更新', 2, NULL, 8, 1, TRUE);
COMMIT;
