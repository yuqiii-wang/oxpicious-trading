-- ============================================================================
--  Table: static.keyword_maps — the seeded keyword → category map
--  (news-text audit vocabulary)
--
--  ONE row per (keyword, category): the literal keyword substring together
--  with the category its occurrence in an article evidences. Replaces the
--  former ``downloads/macro/gov/keywords.json`` file taxonomy (2026-11-01).
--  Categories come in four types (static.label_dim 'kw_category_type'):
--
--    0 industry    — maps to the canonical sec_classification industry_id
--                    (UPPERCASE: POWER_GRID, BANKS, …). Seeded from the
--                    former JSON's industry taxonomy ∪ every catalog
--                    industry's own label (so an article naming just the
--                    label, e.g. 银行 for BANKS, stays taggable).
--    1 market      — market-state descriptions: what the market / a price
--                    leg is doing (static.label_dim 'kw_market': px_up,
--                    px_dn, sharp_up, sharp_dn, gap_up, gap_dn, limit_up,
--                    limit_dn, rebound, pullback, vol_up, vol_dn,
--                    divergence, bullish, bearish, volatile — 下跌 → px_dn,
--                    放量 → vol_up, …). Extracted as keywords, never set an
--                    industry.
--    2 broadmarket — macro-policy themes with no catalog industry
--                    (static.label_dim 'kw_theme': economy, monetary, …).
--                    Extracted as keywords, never set an industry.
--    3 skip        — low-semantic function words (其实, 也, 就是说; the,
--                    a, of — seeded in 05_seed_skip.sql): the search-index
--                    tokenization post-process drops them from every token
--                    stream (_common.tokenization — document vectors stop
--                    indexing them, query terms segment to content tokens
--                    only). Never extracted as keywords by
--                    builds.text.keywords and never tag an industry; both
--                    target columns NULL. Matching is exact-token equality
--                    (a segmented/split word == the keyword,
--                    case-insensitive for ASCII), not substring, so
--                    single-char entries (也) are safe here — unlike the
--                    extraction types, whose rows stay 2+ chars.
--    4..7 component— the grammatical modifier layer that combines with the
--                    market direction verbs in phrases like 大幅上涨 /
--                    高位震荡 / 持续拉升 (seeded in 06_seed_components.sql):
--                    4 adj (强势, 疲弱 — evaluative adjectives), 5 adv
--                    (大幅, 快速 — degree/manner adverbs), 6 adv_noun
--                    (高位, 盘中 — nouns functioning adverbially), 7
--                    adv_verb (持续, 加速 — verbs functioning adverbially).
--                    Like skip they carry no target (both columns NULL)
--                    and are never extracted as audit keywords (a bare
--                    大幅/持续 mention evidences no market state); unlike
--                    skip they STAY in the token streams — content words
--                    the keyword n-gram matcher (keyword_ngrams) composes
--                    with the market verbs.
--
--  STATIC seed layer: rows land ONLY via the seed files — the bootstrap
--  wave-1 files here (02_seed_industry / 03_seed_market /
--  04_seed_broadmarket / 05_seed_skip / 06_seed_components — each
--  re-applies by DELETE + INSERT of its category_type slice, so the live
--  rows stay identical to the files) and the expansion waves under
--  statics/ (05.. — each owns only the keywords it lists, DELETEing and
--  re-INSERTing just those; apply AFTER 02..06, in filename order). Never
--  hand-UPDATE a row; edit the owning seed file and re-apply.
--
--  Consumers build their runtime taxonomies from one read of this table
--  (_common.keyword_maps.load_keyword_maps):
--    * builds.text.keywords.KeywordTaxonomy — news industry tagging +
--      keyword extraction into text.news_keywords (all three types;
--      market / broadmarket keywords are extracted but never set
--      text.news.industry_id);
--    * downloads.macro.gov.articles — gov.cn crawl selection (industry +
--      broadmarket rows only: market descriptions are text-audit
--      vocabulary, not crawl topics).
--  Analysis aggregates extracted mentions by joining
--  text.news_keywords.keyword → keyword here (then the category columns).
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS static;

CREATE TABLE IF NOT EXISTS static.keyword_maps (
    keyword         TEXT        NOT NULL,  -- the literal keyword substring (2+ chars; pure-ASCII matched whole-word case-insensitive by consumers)
    category_type   SMALLINT    NOT NULL,  -- label code (static.label_dim 'kw_category_type'): 0=industry 1=market 2=broadmarket
    industry_id     TEXT,                  -- category_type=0: canonical industry_id (stats.sec_classification); NULL otherwise
    category_code   SMALLINT,              -- category_type=1: 'kw_market' code; category_type=2: 'kw_theme' code; NULL when industry
    parts_of_speech SMALLINT    NOT NULL DEFAULT 0,  -- label code (static.label_dim 'kw_pos'): 0=noun 1=verb 2=noun_verb (dual-class like 回调)
    is_negation     BOOLEAN     NOT NULL DEFAULT false,  -- a preceding negator (没有/并未/未/不/… or English no/not/never) flips the keyword: negated occurrences don't count
    language        SMALLINT    NOT NULL DEFAULT 0,  -- label code (static.label_dim 'kw_language'): 0=zh 1=en — the seed files default to zh; statics/08_seed_english.sql carries en rows explicitly

    -- target-selection rule: an industry row names its industry_id, a
    -- market/broadmarket row its SMALLINT category code, a skip or
    -- component row (3..7) neither — never both.
    CONSTRAINT ck_keyword_maps_target CHECK (
        (category_type = 0 AND industry_id IS NOT NULL AND category_code IS NULL) OR
        (category_type IN (1, 2) AND industry_id IS NULL AND category_code IS NOT NULL) OR
        (category_type >= 3 AND industry_id IS NULL AND category_code IS NULL))
);

-- Migration for tables created before the wave-3 lexical columns
-- (2026-11-01): no-op on fresh installs (CREATE above already has them).
ALTER TABLE static.keyword_maps ADD COLUMN IF NOT EXISTS parts_of_speech SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE static.keyword_maps ADD COLUMN IF NOT EXISTS is_negation BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE static.keyword_maps ADD COLUMN IF NOT EXISTS language SMALLINT NOT NULL DEFAULT 0;

-- Migration for tables created before the skip category type (2026-11-01):
-- rebuild the target-selection CHECK with the no-target branch covering
-- skip AND the component types (no-op on fresh installs — CREATE above
-- already carries it).
ALTER TABLE static.keyword_maps DROP CONSTRAINT IF EXISTS ck_keyword_maps_target;
ALTER TABLE static.keyword_maps ADD CONSTRAINT ck_keyword_maps_target CHECK (
    (category_type = 0 AND industry_id IS NOT NULL AND category_code IS NULL) OR
    (category_type IN (1, 2) AND industry_id IS NULL AND category_code IS NOT NULL) OR
    (category_type >= 3 AND industry_id IS NULL AND category_code IS NULL));

-- Category identity: industry rows key on industry_id (TEXT — the standing
-- industry_id exception), market/broadmarket rows on their SMALLINT code;
-- the COALESCE flattens the two into one unique key per
-- (keyword, category_type, category).
CREATE UNIQUE INDEX IF NOT EXISTS uq_keyword_maps_category
    ON static.keyword_maps (keyword, category_type,
                            COALESCE(industry_id, category_code::TEXT));

COMMENT ON TABLE static.keyword_maps IS
  'Seeded keyword → category map for news-text auditing — one row per '
  '(keyword, category): industry rows (category_type=0, industry_id) tag '
  'articles / extract keywords; market-description rows (1, kw_market '
  'code) and broadmarket macro-theme rows (2, kw_theme code) are extracted '
  'as keywords only; skip rows (3) are the low-semantic function-word '
  'vocabulary the search-index tokenization drops — never extracted; '
  'component rows (4 adj / 5 adv / 6 adv_noun / 7 adv_verb) are the '
  'grammatical modifier layer the keyword n-gram matcher composes with '
  'the market verbs — kept in the token streams, never extracted. '
  'Source of record: the seed files under '
  'database/sql/static/keyword_maps/ (wave-1 bootstrap 02..06, expansion '
  'waves under statics/ — apply in filename order); '
  'consumers read it via _common.keyword_maps.load_keyword_maps.';

COMMENT ON COLUMN static.keyword_maps.keyword IS
  'The literal keyword substring matched against article text by the '
  'consumers (builds.text extraction: plain substring for CJK, whole-word '
  'case-insensitive for pure-ASCII). Extraction rows (industry / market / '
  'broadmarket) are kept 2+ chars so single-char false positives are '
  'avoided; skip rows (category_type=3) match by exact-token equality in '
  'the search-index tokenization (case-insensitive for ASCII), so '
  'single-char entries (也, a) are safe there.';

COMMENT ON COLUMN static.keyword_maps.category_type IS
  'The category family, as its static.label_dim (''kw_category_type'') '
  'SMALLINT code: 0=industry 1=market 2=broadmarket 3=skip 4=adj 5=adv '
  '6=adv_noun 7=adv_verb (4..7 = the grammatical modifier component '
  'families).';

COMMENT ON COLUMN static.keyword_maps.industry_id IS
  'category_type=0 only: the canonical sec_classification industry_id '
  '(UPPERCASE, e.g. POWER_GRID) the keyword maps to — the same values '
  'text.news.industry_id carries, so tags join without mapping. A keyword '
  'may map to several industries (one row each). NULL for market/'
  'broadmarket/skip/component rows.';

COMMENT ON COLUMN static.keyword_maps.category_code IS
  'Sub-type selected by category_type (the external_resource_audit '
  'precedent): the static.label_dim ''kw_market'' code when category_type=1 '
  '(the market-state description: 0=px_up 1=px_dn 2=sharp_up 3=sharp_dn '
  '4=gap_up 5=gap_dn 6=limit_up 7=limit_dn 8=rebound 9=pullback 10=vol_up '
  '11=vol_dn 12=divergence 13=bullish 14=bearish 15=volatile 16=mood '
  '17=position 18=break_up 19=break_dn), the '
  '''kw_theme'' code when category_type=2 (0=economy 1=monetary 2=fiscal '
  '3=trade 4=reform 5=employment 6=environment 7=demographic 8=investment '
  '9=consumption_macro 10=prices 11=industrial_policy '
  '12=financial_stability). NULL for industry, skip and component rows.';

COMMENT ON COLUMN static.keyword_maps.parts_of_speech IS
  'The keyword''s dominant lexical class, as its static.label_dim '
  '(''kw_pos'') SMALLINT code: 0=noun 1=verb 2=noun_verb 3=function. '
  'verb = predicates including adjectives (并不强势 negates like a verb); '
  'noun_verb = dual-class words (回调 as "a pullback" / "to pull back"); '
  'noun = things (银行, 涨幅, 情绪). Industry keywords are topic nouns; '
  'skip rows are function words (adverbs / conjunctions / particles / '
  'discourse markers — no lexical content).';

COMMENT ON COLUMN static.keyword_maps.is_negation IS
  'True when a preceding negation token (没有/并未/尚未/不再/没/不/未/无/非/… '
  'immediately before the match, or English no/not/never/without) FLIPS '
  'the keyword''s meaning — 并未下跌 is '
  'not down-talk, 并非利好 is not bullish tone. The extraction '
  '(builds.text.keywords) drops negated occurrences of flagged keywords '
  'from the counts; seeded true for verb/noun_verb keywords plus the '
  'evaluative nouns (利好/利空/牛市/熊市/恐慌/通胀/通缩), false for topic '
  'nouns (a negated mention still names the topic).';

COMMENT ON COLUMN static.keyword_maps.language IS
  'The keyword''s language, as its static.label_dim (''kw_language'') '
  'SMALLINT code: 0=zh 1=en. The seed files default to zh (the column '
  'DEFAULT); the English rows in statics/08_seed_english.sql carry en '
  'explicitly. English keywords match whole-word and case-insensitive '
  '(multi-word forms bridge whitespace/hyphen: "limit up" matches '
  '"limit-up").';
