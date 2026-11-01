-- ============================================================================
--  static.keyword_maps seed — INDUSTRY EXPANSION (category_type=0).
--
--  Wave 2 (2026-11-01): industry keywords mined from the corpus (frequency
--  in parentheses) that name a catalog industry's hot sub-theme without
--  being the label itself. Every industry_id is verified against the
--  sec_classification catalog. THIS FILE owns exactly the keywords listed
--  below; the bootstrap slice file (../02_seed_industry.sql) still owns
--  the wave-1 rows. Apply AFTER 02..04 (02's slice DELETE would otherwise
--  wipe these rows). Re-applies by DELETE of its own keywords within the
--  slice + INSERT — run the whole file in ONE transaction.
-- ============================================================================

BEGIN;
DELETE FROM static.keyword_maps
WHERE category_type = 0
  AND keyword = ANY (ARRAY[
    '光模块', '跨境电商', '冰雪经济'
  ]);
INSERT INTO static.keyword_maps (keyword, category_type, industry_id, category_code, parts_of_speech, is_negation) VALUES
    -- 光模块 (56): the optical-module sub-sector — the COMMS industry's
    -- dominant AI-capex theme
    ('光模块', 0, 'COMMS', NULL, 0, FALSE),
    -- 跨境电商 (18): cross-border e-commerce, the INTERNET industry's
    -- trade-adjacent theme
    ('跨境电商', 0, 'INTERNET', NULL, 0, FALSE),
    -- 冰雪经济 (13): the ice-and-snow economy program, a TOURISM theme
    ('冰雪经济', 0, 'TOURISM', NULL, 0, FALSE);
COMMIT;
