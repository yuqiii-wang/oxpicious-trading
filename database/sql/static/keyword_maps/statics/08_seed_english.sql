-- ============================================================================
--  static.keyword_maps seed — ENGLISH rows (language=1, wave 4).
--
--  English equivalents of the curated vocabulary across all three category
--  types, for English-language sources and mixed-language text. THIS FILE
--  owns exactly the keywords listed below (three DELETE+INSERT sections,
--  one per category_type); the zh files (../02..04, ../statics/05..07)
--  own the Chinese rows. Apply AFTER those files (in filename order).
--  Re-applies by DELETE of its own keywords within each slice + INSERT —
--  run the whole file in ONE transaction.
--
--  Matching semantics (builds.text.keywords): English keywords match
--  whole-word and case-insensitive; multi-word forms bridge whitespace
--  and hyphen, so "limit up" also matches "limit-up" and "Risk-Off".
--  Only base/inflected forms that headlines actually print are seeded —
--  no tense combinatorics. Market rows are negation-flagged like their
--  zh counterparts ("no rebound" / "not falling" don't count); broadmarket
--  event nouns are flagged where "no X" is a real headline ("no rate
--  cut"); industry topic nouns are not.
-- ============================================================================

BEGIN;
DELETE FROM static.keyword_maps
WHERE category_type = 1 AND language = 1
  AND keyword = ANY (ARRAY[
    'rise', 'rises', 'gains', 'gain', 'rising',
    'fall', 'falls', 'decline', 'declines', 'drop', 'drops', 'slip', 'slips', 'weaken', 'falling',
    'surge', 'surges', 'soar', 'soars', 'spike', 'spikes', 'surging',
    'plunge', 'plunges', 'crash', 'crashes', 'slump', 'slumps', 'tumble', 'tumbles', 'plunging',
    'gap up', 'opens higher', 'gap down', 'opens lower',
    'limit up', 'limit down',
    'rebound', 'rebounds', 'bounce', 'bounces', 'recovery', 'recover',
    'pullback', 'pullbacks', 'retreat', 'retreats', 'correction', 'consolidation', 'retreating',
    'inflow', 'inflows', 'volume surge', 'heavy volume',
    'outflow', 'outflows', 'low volume',
    'divergence',
    'bullish', 'upbeat', 'optimistic',
    'bearish', 'negative', 'pessimistic',
    'volatile', 'volatility', 'choppy', 'range-bound',
    'sentiment', 'panic', 'euphoria', 'optimism', 'pessimism',
    'risk appetite', 'risk-on', 'risk-off', 'cautious',
    'profit-taking', 'rotation', 'rotate', 'bottom fishing',
    'breakout', 'breakouts', 'record high', 'all-time high', 'new high',
    'breakdown', 'break below', 'record low', 'new low'
  ]);
INSERT INTO static.keyword_maps
    (keyword, category_type, industry_id, category_code, parts_of_speech, is_negation, language) VALUES
    -- px_up (0) — headline nouns (gains) + verbs + the headline -ing forms
    ('rise', 1, NULL, 0, 1, TRUE, 1),
    ('rises', 1, NULL, 0, 1, TRUE, 1),
    ('gains', 1, NULL, 0, 0, TRUE, 1),
    ('gain', 1, NULL, 0, 2, TRUE, 1),
    ('rising', 1, NULL, 0, 1, TRUE, 1),
    -- px_dn (1)
    ('fall', 1, NULL, 1, 1, TRUE, 1),
    ('falls', 1, NULL, 1, 1, TRUE, 1),
    ('decline', 1, NULL, 1, 2, TRUE, 1),
    ('declines', 1, NULL, 1, 1, TRUE, 1),
    ('drop', 1, NULL, 1, 2, TRUE, 1),
    ('drops', 1, NULL, 1, 1, TRUE, 1),
    ('slip', 1, NULL, 1, 1, TRUE, 1),
    ('slips', 1, NULL, 1, 1, TRUE, 1),
    ('weaken', 1, NULL, 1, 1, TRUE, 1),
    ('falling', 1, NULL, 1, 1, TRUE, 1),
    -- sharp_up (2)
    ('surge', 1, NULL, 2, 2, TRUE, 1),
    ('surges', 1, NULL, 2, 1, TRUE, 1),
    ('soar', 1, NULL, 2, 1, TRUE, 1),
    ('soars', 1, NULL, 2, 1, TRUE, 1),
    ('spike', 1, NULL, 2, 2, TRUE, 1),
    ('spikes', 1, NULL, 2, 1, TRUE, 1),
    ('surging', 1, NULL, 2, 1, TRUE, 1),
    -- sharp_dn (3)
    ('plunge', 1, NULL, 3, 2, TRUE, 1),
    ('plunges', 1, NULL, 3, 1, TRUE, 1),
    ('crash', 1, NULL, 3, 2, TRUE, 1),
    ('crashes', 1, NULL, 3, 1, TRUE, 1),
    ('slump', 1, NULL, 3, 2, TRUE, 1),
    ('slumps', 1, NULL, 3, 1, TRUE, 1),
    ('tumble', 1, NULL, 3, 1, TRUE, 1),
    ('tumbles', 1, NULL, 3, 1, TRUE, 1),
    ('plunging', 1, NULL, 3, 1, TRUE, 1),
    -- gaps (4/5)
    ('gap up', 1, NULL, 4, 1, TRUE, 1),
    ('opens higher', 1, NULL, 4, 1, TRUE, 1),
    ('gap down', 1, NULL, 5, 1, TRUE, 1),
    ('opens lower', 1, NULL, 5, 1, TRUE, 1),
    -- limits (6/7) — "limit up" bridges "limit-up" via the matcher
    ('limit up', 1, NULL, 6, 0, TRUE, 1),
    ('limit down', 1, NULL, 7, 0, TRUE, 1),
    -- rebound (8) / pullback (9)
    ('rebound', 1, NULL, 8, 2, TRUE, 1),
    ('rebounds', 1, NULL, 8, 1, TRUE, 1),
    ('bounce', 1, NULL, 8, 2, TRUE, 1),
    ('bounces', 1, NULL, 8, 1, TRUE, 1),
    ('recovery', 1, NULL, 8, 0, TRUE, 1),
    ('recover', 1, NULL, 8, 1, TRUE, 1),
    ('pullback', 1, NULL, 9, 0, TRUE, 1),
    ('pullbacks', 1, NULL, 9, 0, TRUE, 1),
    ('retreat', 1, NULL, 9, 2, TRUE, 1),
    ('retreats', 1, NULL, 9, 1, TRUE, 1),
    ('correction', 1, NULL, 9, 0, TRUE, 1),
    ('consolidation', 1, NULL, 9, 0, TRUE, 1),
    -- participation (10/11)
    ('inflow', 1, NULL, 10, 0, TRUE, 1),
    ('inflows', 1, NULL, 10, 0, TRUE, 1),
    ('volume surge', 1, NULL, 10, 0, TRUE, 1),
    ('heavy volume', 1, NULL, 10, 0, TRUE, 1),
    ('outflow', 1, NULL, 11, 0, TRUE, 1),
    ('outflows', 1, NULL, 11, 0, TRUE, 1),
    ('low volume', 1, NULL, 11, 0, TRUE, 1),
    -- divergence (12)
    ('divergence', 1, NULL, 12, 0, TRUE, 1),
    -- tone (13/14) — adjectives count as predicates (verb class)
    ('bullish', 1, NULL, 13, 1, TRUE, 1),
    ('upbeat', 1, NULL, 13, 1, TRUE, 1),
    ('optimistic', 1, NULL, 13, 1, TRUE, 1),
    ('bearish', 1, NULL, 14, 1, TRUE, 1),
    ('negative', 1, NULL, 14, 1, TRUE, 1),
    ('pessimistic', 1, NULL, 14, 1, TRUE, 1),
    -- volatile (15)
    ('volatile', 1, NULL, 15, 1, TRUE, 1),
    ('volatility', 1, NULL, 15, 0, TRUE, 1),
    ('choppy', 1, NULL, 15, 1, TRUE, 1),
    ('range-bound', 1, NULL, 15, 1, TRUE, 1),
    -- mood (16)
    ('sentiment', 1, NULL, 16, 0, TRUE, 1),
    ('panic', 1, NULL, 16, 0, TRUE, 1),
    ('euphoria', 1, NULL, 16, 0, TRUE, 1),
    ('optimism', 1, NULL, 16, 0, TRUE, 1),
    ('pessimism', 1, NULL, 16, 0, TRUE, 1),
    ('risk appetite', 1, NULL, 16, 0, TRUE, 1),
    ('risk-on', 1, NULL, 16, 0, TRUE, 1),
    ('risk-off', 1, NULL, 16, 0, TRUE, 1),
    ('cautious', 1, NULL, 16, 1, TRUE, 1),
    -- position (17)
    ('profit-taking', 1, NULL, 17, 0, TRUE, 1),
    ('rotation', 1, NULL, 17, 0, TRUE, 1),
    ('rotate', 1, NULL, 17, 1, TRUE, 1),
    ('bottom fishing', 1, NULL, 17, 0, TRUE, 1),
    -- break_up (18) / break_dn (19)
    ('breakout', 1, NULL, 18, 0, TRUE, 1),
    ('breakouts', 1, NULL, 18, 0, TRUE, 1),
    ('record high', 1, NULL, 18, 0, TRUE, 1),
    ('all-time high', 1, NULL, 18, 0, TRUE, 1),
    ('new high', 1, NULL, 18, 0, TRUE, 1),
    ('breakdown', 1, NULL, 19, 0, TRUE, 1),
    ('break below', 1, NULL, 19, 1, TRUE, 1),
    ('record low', 1, NULL, 19, 0, TRUE, 1),
    ('new low', 1, NULL, 19, 0, TRUE, 1);

DELETE FROM static.keyword_maps
WHERE category_type = 2 AND language = 1
  AND keyword = ANY (ARRAY[
    'rate cut', 'rate hike',
    'stimulus', 'tax cut', 'tax cuts',
    'trade war', 'tariffs', 'tariff', 'export controls',
    'inflation', 'deflation',
    'recession', 'hard landing', 'soft landing',
    'unemployment', 'nonfarm payrolls',
    'financial stability', 'systemic risk',
    'industrial policy'
  ]);
INSERT INTO static.keyword_maps
    (keyword, category_type, industry_id, category_code, parts_of_speech, is_negation, language) VALUES
    -- monetary (1) — "no rate cut" is a real headline, so flagged
    ('rate cut', 2, NULL, 1, 0, TRUE, 1),
    ('rate hike', 2, NULL, 1, 0, TRUE, 1),
    -- fiscal (2)
    ('stimulus', 2, NULL, 2, 0, TRUE, 1),
    ('tax cut', 2, NULL, 2, 0, TRUE, 1),
    ('tax cuts', 2, NULL, 2, 0, TRUE, 1),
    -- trade (3)
    ('trade war', 2, NULL, 3, 0, FALSE, 1),
    ('tariffs', 2, NULL, 3, 0, FALSE, 1),
    ('tariff', 2, NULL, 3, 0, FALSE, 1),
    ('export controls', 2, NULL, 3, 0, FALSE, 1),
    -- prices (10)
    ('inflation', 2, NULL, 10, 0, TRUE, 1),
    ('deflation', 2, NULL, 10, 0, TRUE, 1),
    -- economy (0)
    ('recession', 2, NULL, 0, 0, TRUE, 1),
    ('hard landing', 2, NULL, 0, 0, FALSE, 1),
    ('soft landing', 2, NULL, 0, 0, FALSE, 1),
    -- employment (5)
    ('unemployment', 2, NULL, 5, 0, TRUE, 1),
    ('nonfarm payrolls', 2, NULL, 5, 0, FALSE, 1),
    -- financial_stability (12)
    ('financial stability', 2, NULL, 12, 0, FALSE, 1),
    ('systemic risk', 2, NULL, 12, 0, FALSE, 1),
    -- industrial_policy (11)
    ('industrial policy', 2, NULL, 11, 0, FALSE, 1);

DELETE FROM static.keyword_maps
WHERE category_type = 0 AND language = 1
  AND keyword = ANY (ARRAY[
    'semiconductor', 'semiconductors', 'chip', 'chips', 'wafer',
    'artificial intelligence',
    'EV', 'EVs', 'electric vehicle', 'electric vehicles',
    'solid-state battery', 'battery', 'batteries',
    'e-commerce', 'cross-border e-commerce',
    'bank', 'banks', 'broker', 'brokers',
    'optical module'
  ]);
INSERT INTO static.keyword_maps
    (keyword, category_type, industry_id, category_code, parts_of_speech, is_negation, language) VALUES
    -- SEMI
    ('semiconductor', 0, 'SEMI', NULL, 0, FALSE, 1),
    ('semiconductors', 0, 'SEMI', NULL, 0, FALSE, 1),
    ('chip', 0, 'SEMI', NULL, 0, FALSE, 1),
    ('chips', 0, 'SEMI', NULL, 0, FALSE, 1),
    ('wafer', 0, 'SEMI', NULL, 0, FALSE, 1),
    -- AI
    ('artificial intelligence', 0, 'AI', NULL, 0, FALSE, 1),
    -- EV
    ('EV', 0, 'EV', NULL, 0, FALSE, 1),
    ('EVs', 0, 'EV', NULL, 0, FALSE, 1),
    ('electric vehicle', 0, 'EV', NULL, 0, FALSE, 1),
    ('electric vehicles', 0, 'EV', NULL, 0, FALSE, 1),
    -- battery
    ('solid-state battery', 0, 'BATTERY', NULL, 0, FALSE, 1),
    ('battery', 0, 'BATTERY', NULL, 0, FALSE, 1),
    ('batteries', 0, 'BATTERY', NULL, 0, FALSE, 1),
    -- internet
    ('e-commerce', 0, 'INTERNET', NULL, 0, FALSE, 1),
    ('cross-border e-commerce', 0, 'INTERNET', NULL, 0, FALSE, 1),
    -- banks / brokers
    ('bank', 0, 'BANKS', NULL, 0, FALSE, 1),
    ('banks', 0, 'BANKS', NULL, 0, FALSE, 1),
    ('broker', 0, 'BROKERS', NULL, 0, FALSE, 1),
    ('brokers', 0, 'BROKERS', NULL, 0, FALSE, 1),
    -- comms
    ('optical module', 0, 'COMMS', NULL, 0, FALSE, 1);
COMMIT;
