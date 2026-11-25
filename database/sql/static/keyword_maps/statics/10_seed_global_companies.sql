-- ============================================================================
--  static.keyword_maps seed — GLOBAL RENOWNED COMPANIES (category_type=0).
--
--  Wave 5 (2026-11-21): the internationally renowned companies whose name in
--  an article evidences their industry — each company seeded with its English
--  (ticker/brand, language=1) and Chinese (language=0) forms where a common
--  zh name exists, mapped to the canonical sec_classification industry_id
--  (every industry_id verified against the catalog JSONs under
--  database/sec_statics/sec_classification/catalog/). Industry rows, so an
--  article naming 英伟达/NVIDIA is tagged SEMI and extracts the keyword —
--  the same path the catalog labels take.
--
--  THIS FILE owns exactly the keywords listed below (ONE DELETE+INSERT pair,
--  both languages); the bootstrap slice file (../02_seed_industry.sql) and
--  the other expansion waves own their own rows. Apply AFTER 02..09 (in
--  filename order — 02's slice DELETE would otherwise wipe these rows).
--  Re-applies by DELETE of its own keywords within the slice + INSERT — run
--  the whole file in ONE transaction.
--
--  Matching semantics (builds.text.keywords): en keywords match whole-word
--  and case-insensitively (nvidia hits NVIDIA/Nvidia); zh keywords match as
--  plain substrings. Seeded zh names avoid ambiguous bare forms — 理想汽车
--  (not 理想), 大众/奔驰/现代 dropped (common-word collisions), 阿里巴巴
--  (not 阿里), 力拓 kept English-only. All rows are nouns, never
--  negation-sensitive (a company name's reading doesn't flip).
-- ============================================================================

BEGIN;
DELETE FROM static.keyword_maps
WHERE category_type = 0
  AND keyword = ANY (ARRAY[
    -- SEMI
    'nvidia', '英伟达', 'tsmc', '台积电', 'amd', 'intel', '英特尔',
    'qualcomm', '高通', 'broadcom', '博通', 'asml', '阿斯麦',
    'micron', '美光', 'sk hynix', 'SK海力士', '海力士',
    'texas instruments', '德州仪器', 'arm holdings',
    -- SEMI + CONSUMER_ELEC (Samsung spans both; rule order picks SEMI)
    'samsung', '三星',
    -- CONSUMER_ELEC
    'apple', '苹果', 'sony', '索尼', 'xiaomi', '小米', 'foxconn', '富士康',
    -- COMPUTER
    'ibm', 'dell', '戴尔', '惠普',
    -- SOFTWARE
    'microsoft', '微软', 'oracle', '甲骨文', 'sap', 'salesforce', 'adobe',
    'palantir', 'servicenow',
    -- INTERNET
    'google', 'alphabet', '谷歌', 'amazon', '亚马逊', 'meta',
    'alibaba', '阿里巴巴', 'tencent', '腾讯', 'baidu', '百度',
    '京东', '拼多多', 'meituan', '美团', 'netease', '网易', 'uber',
    -- COMMS
    'huawei', '华为', 'cisco', '思科', 'nokia', '诺基亚', 'ericsson', '爱立信',
    -- AI
    'openai', 'anthropic', 'deepmind',
    -- EV
    'tesla', '特斯拉', 'byd', '比亚迪', 'nio', '蔚来', 'xpeng', '小鹏',
    'li auto', '理想汽车',
    -- AUTO
    'toyota', '丰田', 'bmw', '宝马', 'mercedes', 'ford', '福特',
    'honda', '本田', 'hyundai', 'volkswagen', 'general motors', '通用汽车',
    'ferrari', '法拉利', 'porsche', '保时捷',
    -- OIL / OIL_GAS
    'exxonmobil', '埃克森美孚', 'chevron', '雪佛龙',
    '壳牌', 'saudi aramco', '沙特阿美',
    -- BANKS
    'jpmorgan', 'jp morgan', '摩根大通', 'citigroup', '花旗',
    'bank of america', '美国银行', 'hsbc', '汇丰',
    'deutsche bank', '德意志银行',
    -- BROKERS
    'goldman sachs', '高盛', 'morgan stanley', '摩根士丹利',
    'ubs', '瑞银', 'barclays', '巴克莱',
    -- FIN_GENERAL
    'blackrock', '贝莱德', 'blackstone', '黑石',
    'berkshire hathaway', '伯克希尔', 'bridgewater', '桥水',
    -- INNO_DRUG
    'pfizer', '辉瑞', 'eli lilly', '礼来', 'astrazeneca', '阿斯利康',
    'novartis', '诺华', 'roche', '罗氏', 'merck', '默沙东',
    'novo nordisk', '诺和诺德', 'abbvie', '艾伯维', 'sanofi', '赛诺菲',
    'gsk', '葛兰素史克',
    -- VACCINE
    'biontech', 'moderna', '莫德纳',
    -- PHARMA_BROAD
    '强生',
    -- AERO
    'boeing', '波音', 'airbus', '空客', 'spacex',
    -- INDUSTRIAL
    'caterpillar', '卡特彼勒', 'siemens', '西门子', '通用电气',
    -- MEDIA / GAMES
    'disney', '迪士尼', 'netflix', '奈飞', 'nintendo', '任天堂',
    -- SPORTS
    'nike', '耐克', 'adidas', '阿迪达斯',
    -- FOOD_BEV
    'coca cola', '可口可乐', 'pepsi', '百事可乐', 'mcdonald', '麦当劳',
    -- RETAIL
    'walmart', '沃尔玛', 'costco', '开市客',
    -- DISCRETIONARY (luxury / FMCG)
    'lvmh', '路威酩轩', 'hermes', '爱马仕', '宝洁', 'unilever', '联合利华',
    -- METALS (mining)
    'bhp', '必和必拓', 'rio tinto', 'vale', '淡水河谷'
  ]);
INSERT INTO static.keyword_maps
    (keyword, category_type, industry_id, category_code, parts_of_speech,
     is_negation, language) VALUES
    -- ============ SEMI — semiconductors (chips / equipment / memory) ========
    ('nvidia',            0, 'SEMI', NULL, 0, FALSE, 1),
    ('英伟达',             0, 'SEMI', NULL, 0, FALSE, 0),
    ('tsmc',              0, 'SEMI', NULL, 0, FALSE, 1),
    ('台积电',             0, 'SEMI', NULL, 0, FALSE, 0),
    ('amd',               0, 'SEMI', NULL, 0, FALSE, 1),
    ('intel',             0, 'SEMI', NULL, 0, FALSE, 1),
    ('英特尔',             0, 'SEMI', NULL, 0, FALSE, 0),
    ('qualcomm',          0, 'SEMI', NULL, 0, FALSE, 1),
    ('高通',               0, 'SEMI', NULL, 0, FALSE, 0),
    ('broadcom',          0, 'SEMI', NULL, 0, FALSE, 1),
    ('博通',               0, 'SEMI', NULL, 0, FALSE, 0),
    ('asml',              0, 'SEMI', NULL, 0, FALSE, 1),
    ('阿斯麦',             0, 'SEMI', NULL, 0, FALSE, 0),
    ('micron',            0, 'SEMI', NULL, 0, FALSE, 1),
    ('美光',               0, 'SEMI', NULL, 0, FALSE, 0),
    ('sk hynix',          0, 'SEMI', NULL, 0, FALSE, 1),
    ('SK海力士',           0, 'SEMI', NULL, 0, FALSE, 0),
    ('海力士',             0, 'SEMI', NULL, 0, FALSE, 0),
    ('texas instruments', 0, 'SEMI', NULL, 0, FALSE, 1),
    ('德州仪器',           0, 'SEMI', NULL, 0, FALSE, 0),
    ('arm holdings',      0, 'SEMI', NULL, 0, FALSE, 1),
    -- Samsung spans chips and devices: two rows each language — the
    -- extraction unions the industries and rule order picks SEMI.
    ('samsung',           0, 'SEMI', NULL, 0, FALSE, 1),
    ('三星',               0, 'SEMI', NULL, 0, FALSE, 0),
    -- ============ CONSUMER_ELEC ============================================
    ('samsung',           0, 'CONSUMER_ELEC', NULL, 0, FALSE, 1),
    ('三星',               0, 'CONSUMER_ELEC', NULL, 0, FALSE, 0),
    ('apple',             0, 'CONSUMER_ELEC', NULL, 0, FALSE, 1),
    ('苹果',               0, 'CONSUMER_ELEC', NULL, 0, FALSE, 0),
    ('sony',              0, 'CONSUMER_ELEC', NULL, 0, FALSE, 1),
    ('索尼',               0, 'CONSUMER_ELEC', NULL, 0, FALSE, 0),
    ('xiaomi',            0, 'CONSUMER_ELEC', NULL, 0, FALSE, 1),
    ('小米',               0, 'CONSUMER_ELEC', NULL, 0, FALSE, 0),
    ('foxconn',           0, 'CONSUMER_ELEC', NULL, 0, FALSE, 1),
    ('富士康',             0, 'CONSUMER_ELEC', NULL, 0, FALSE, 0),
    -- ============ COMPUTER =================================================
    ('ibm',               0, 'COMPUTER', NULL, 0, FALSE, 1),
    ('dell',              0, 'COMPUTER', NULL, 0, FALSE, 1),
    ('戴尔',               0, 'COMPUTER', NULL, 0, FALSE, 0),
    ('惠普',               0, 'COMPUTER', NULL, 0, FALSE, 0),
    -- ============ SOFTWARE =================================================
    ('microsoft',         0, 'SOFTWARE', NULL, 0, FALSE, 1),
    ('微软',               0, 'SOFTWARE', NULL, 0, FALSE, 0),
    ('oracle',            0, 'SOFTWARE', NULL, 0, FALSE, 1),
    ('甲骨文',             0, 'SOFTWARE', NULL, 0, FALSE, 0),
    ('sap',               0, 'SOFTWARE', NULL, 0, FALSE, 1),
    ('salesforce',        0, 'SOFTWARE', NULL, 0, FALSE, 1),
    ('adobe',             0, 'SOFTWARE', NULL, 0, FALSE, 1),
    ('palantir',          0, 'SOFTWARE', NULL, 0, FALSE, 1),
    ('servicenow',        0, 'SOFTWARE', NULL, 0, FALSE, 1),
    -- ============ INTERNET =================================================
    ('google',            0, 'INTERNET', NULL, 0, FALSE, 1),
    ('alphabet',          0, 'INTERNET', NULL, 0, FALSE, 1),
    ('谷歌',               0, 'INTERNET', NULL, 0, FALSE, 0),
    ('amazon',            0, 'INTERNET', NULL, 0, FALSE, 1),
    ('亚马逊',             0, 'INTERNET', NULL, 0, FALSE, 0),
    ('meta',              0, 'INTERNET', NULL, 0, FALSE, 1),
    ('alibaba',           0, 'INTERNET', NULL, 0, FALSE, 1),
    ('阿里巴巴',           0, 'INTERNET', NULL, 0, FALSE, 0),
    ('tencent',           0, 'INTERNET', NULL, 0, FALSE, 1),
    ('腾讯',               0, 'INTERNET', NULL, 0, FALSE, 0),
    ('baidu',             0, 'INTERNET', NULL, 0, FALSE, 1),
    ('百度',               0, 'INTERNET', NULL, 0, FALSE, 0),
    ('京东',               0, 'INTERNET', NULL, 0, FALSE, 0),
    ('拼多多',             0, 'INTERNET', NULL, 0, FALSE, 0),
    ('meituan',           0, 'INTERNET', NULL, 0, FALSE, 1),
    ('美团',               0, 'INTERNET', NULL, 0, FALSE, 0),
    ('netease',           0, 'INTERNET', NULL, 0, FALSE, 1),
    ('网易',               0, 'INTERNET', NULL, 0, FALSE, 0),
    ('uber',              0, 'INTERNET', NULL, 0, FALSE, 1),
    -- ============ COMMS — telecom equipment ================================
    ('huawei',            0, 'COMMS', NULL, 0, FALSE, 1),
    ('华为',               0, 'COMMS', NULL, 0, FALSE, 0),
    ('cisco',             0, 'COMMS', NULL, 0, FALSE, 1),
    ('思科',               0, 'COMMS', NULL, 0, FALSE, 0),
    ('nokia',             0, 'COMMS', NULL, 0, FALSE, 1),
    ('诺基亚',             0, 'COMMS', NULL, 0, FALSE, 0),
    ('ericsson',          0, 'COMMS', NULL, 0, FALSE, 1),
    ('爱立信',             0, 'COMMS', NULL, 0, FALSE, 0),
    -- ============ AI =======================================================
    ('openai',            0, 'AI', NULL, 0, FALSE, 1),
    ('anthropic',         0, 'AI', NULL, 0, FALSE, 1),
    ('deepmind',          0, 'AI', NULL, 0, FALSE, 1),
    -- ============ EV — new energy vehicles =================================
    ('tesla',             0, 'EV', NULL, 0, FALSE, 1),
    ('特斯拉',             0, 'EV', NULL, 0, FALSE, 0),
    ('byd',               0, 'EV', NULL, 0, FALSE, 1),
    ('比亚迪',             0, 'EV', NULL, 0, FALSE, 0),
    ('nio',               0, 'EV', NULL, 0, FALSE, 1),
    ('蔚来',               0, 'EV', NULL, 0, FALSE, 0),
    ('xpeng',             0, 'EV', NULL, 0, FALSE, 1),
    ('小鹏',               0, 'EV', NULL, 0, FALSE, 0),
    ('li auto',           0, 'EV', NULL, 0, FALSE, 1),
    ('理想汽车',           0, 'EV', NULL, 0, FALSE, 0),
    -- ============ AUTO =====================================================
    ('toyota',            0, 'AUTO', NULL, 0, FALSE, 1),
    ('丰田',               0, 'AUTO', NULL, 0, FALSE, 0),
    ('bmw',               0, 'AUTO', NULL, 0, FALSE, 1),
    ('宝马',               0, 'AUTO', NULL, 0, FALSE, 0),
    ('mercedes',          0, 'AUTO', NULL, 0, FALSE, 1),
    ('ford',              0, 'AUTO', NULL, 0, FALSE, 1),
    ('福特',               0, 'AUTO', NULL, 0, FALSE, 0),
    ('honda',             0, 'AUTO', NULL, 0, FALSE, 1),
    ('本田',               0, 'AUTO', NULL, 0, FALSE, 0),
    ('hyundai',           0, 'AUTO', NULL, 0, FALSE, 1),
    ('volkswagen',        0, 'AUTO', NULL, 0, FALSE, 1),
    ('general motors',    0, 'AUTO', NULL, 0, FALSE, 1),
    ('通用汽车',           0, 'AUTO', NULL, 0, FALSE, 0),
    ('ferrari',           0, 'AUTO', NULL, 0, FALSE, 1),
    ('法拉利',             0, 'AUTO', NULL, 0, FALSE, 0),
    ('porsche',           0, 'AUTO', NULL, 0, FALSE, 1),
    ('保时捷',             0, 'AUTO', NULL, 0, FALSE, 0),
    -- ============ OIL / OIL_GAS ============================================
    ('exxonmobil',        0, 'OIL', NULL, 0, FALSE, 1),
    ('埃克森美孚',         0, 'OIL', NULL, 0, FALSE, 0),
    ('chevron',           0, 'OIL', NULL, 0, FALSE, 1),
    ('雪佛龙',             0, 'OIL', NULL, 0, FALSE, 0),
    -- 'shell' skipped: the whole-word en form also hits "shell company".
    ('壳牌',               0, 'OIL_GAS', NULL, 0, FALSE, 0),
    ('saudi aramco',      0, 'OIL_GAS', NULL, 0, FALSE, 1),
    ('沙特阿美',           0, 'OIL_GAS', NULL, 0, FALSE, 0),
    -- ============ BANKS ====================================================
    ('jpmorgan',          0, 'BANKS', NULL, 0, FALSE, 1),
    ('jp morgan',         0, 'BANKS', NULL, 0, FALSE, 1),
    ('摩根大通',           0, 'BANKS', NULL, 0, FALSE, 0),
    ('citigroup',         0, 'BANKS', NULL, 0, FALSE, 1),
    ('花旗',               0, 'BANKS', NULL, 0, FALSE, 0),
    ('bank of america',   0, 'BANKS', NULL, 0, FALSE, 1),
    ('美国银行',           0, 'BANKS', NULL, 0, FALSE, 0),
    ('hsbc',              0, 'BANKS', NULL, 0, FALSE, 1),
    ('汇丰',               0, 'BANKS', NULL, 0, FALSE, 0),
    ('deutsche bank',     0, 'BANKS', NULL, 0, FALSE, 1),
    ('德意志银行',         0, 'BANKS', NULL, 0, FALSE, 0),
    -- ============ BROKERS — investment banks / asset managers ==============
    ('goldman sachs',     0, 'BROKERS', NULL, 0, FALSE, 1),
    ('高盛',               0, 'BROKERS', NULL, 0, FALSE, 0),
    ('morgan stanley',    0, 'BROKERS', NULL, 0, FALSE, 1),
    ('摩根士丹利',         0, 'BROKERS', NULL, 0, FALSE, 0),
    ('ubs',               0, 'BROKERS', NULL, 0, FALSE, 1),
    ('瑞银',               0, 'BROKERS', NULL, 0, FALSE, 0),
    ('barclays',          0, 'BROKERS', NULL, 0, FALSE, 1),
    ('巴克莱',             0, 'BROKERS', NULL, 0, FALSE, 0),
    -- ============ FIN_GENERAL — the non-bank finance names =================
    ('blackrock',         0, 'FIN_GENERAL', NULL, 0, FALSE, 1),
    ('贝莱德',             0, 'FIN_GENERAL', NULL, 0, FALSE, 0),
    ('blackstone',        0, 'FIN_GENERAL', NULL, 0, FALSE, 1),
    ('黑石',               0, 'FIN_GENERAL', NULL, 0, FALSE, 0),
    ('berkshire hathaway', 0, 'FIN_GENERAL', NULL, 0, FALSE, 1),
    ('伯克希尔',           0, 'FIN_GENERAL', NULL, 0, FALSE, 0),
    ('bridgewater',       0, 'FIN_GENERAL', NULL, 0, FALSE, 1),
    ('桥水',               0, 'FIN_GENERAL', NULL, 0, FALSE, 0),
    -- ============ INNO_DRUG ================================================
    ('pfizer',            0, 'INNO_DRUG', NULL, 0, FALSE, 1),
    ('辉瑞',               0, 'INNO_DRUG', NULL, 0, FALSE, 0),
    ('eli lilly',         0, 'INNO_DRUG', NULL, 0, FALSE, 1),
    ('礼来',               0, 'INNO_DRUG', NULL, 0, FALSE, 0),
    ('astrazeneca',       0, 'INNO_DRUG', NULL, 0, FALSE, 1),
    ('阿斯利康',           0, 'INNO_DRUG', NULL, 0, FALSE, 0),
    ('novartis',          0, 'INNO_DRUG', NULL, 0, FALSE, 1),
    ('诺华',               0, 'INNO_DRUG', NULL, 0, FALSE, 0),
    ('roche',             0, 'INNO_DRUG', NULL, 0, FALSE, 1),
    ('罗氏',               0, 'INNO_DRUG', NULL, 0, FALSE, 0),
    ('merck',             0, 'INNO_DRUG', NULL, 0, FALSE, 1),
    ('默沙东',             0, 'INNO_DRUG', NULL, 0, FALSE, 0),
    ('novo nordisk',      0, 'INNO_DRUG', NULL, 0, FALSE, 1),
    ('诺和诺德',           0, 'INNO_DRUG', NULL, 0, FALSE, 0),
    ('abbvie',            0, 'INNO_DRUG', NULL, 0, FALSE, 1),
    ('艾伯维',             0, 'INNO_DRUG', NULL, 0, FALSE, 0),
    ('sanofi',            0, 'INNO_DRUG', NULL, 0, FALSE, 1),
    ('赛诺菲',             0, 'INNO_DRUG', NULL, 0, FALSE, 0),
    ('gsk',               0, 'INNO_DRUG', NULL, 0, FALSE, 1),
    ('葛兰素史克',         0, 'INNO_DRUG', NULL, 0, FALSE, 0),
    -- ============ VACCINE ==================================================
    ('biontech',          0, 'VACCINE', NULL, 0, FALSE, 1),
    ('moderna',           0, 'VACCINE', NULL, 0, FALSE, 1),
    ('莫德纳',             0, 'VACCINE', NULL, 0, FALSE, 0),
    -- ============ PHARMA_BROAD =============================================
    ('强生',               0, 'PHARMA_BROAD', NULL, 0, FALSE, 0),
    -- ============ AERO =====================================================
    ('boeing',            0, 'AERO_AVIATION', NULL, 0, FALSE, 1),
    ('波音',               0, 'AERO_AVIATION', NULL, 0, FALSE, 0),
    ('airbus',            0, 'AERO_AVIATION', NULL, 0, FALSE, 1),
    ('空客',               0, 'AERO_AVIATION', NULL, 0, FALSE, 0),
    ('spacex',            0, 'AERO_SPACE', NULL, 0, FALSE, 1),
    -- ============ INDUSTRIAL ===============================================
    ('caterpillar',       0, 'ENG_MACHINERY', NULL, 0, FALSE, 1),
    ('卡特彼勒',           0, 'ENG_MACHINERY', NULL, 0, FALSE, 0),
    ('siemens',           0, 'POWER_EQUIP', NULL, 0, FALSE, 1),
    ('西门子',             0, 'POWER_EQUIP', NULL, 0, FALSE, 0),
    ('通用电气',           0, 'IND_GENERAL', NULL, 0, FALSE, 0),
    -- ============ MEDIA / GAMES ============================================
    ('disney',            0, 'MEDIA', NULL, 0, FALSE, 1),
    ('迪士尼',             0, 'MEDIA', NULL, 0, FALSE, 0),
    ('netflix',           0, 'MEDIA', NULL, 0, FALSE, 1),
    ('奈飞',               0, 'MEDIA', NULL, 0, FALSE, 0),
    ('nintendo',          0, 'GAMES', NULL, 0, FALSE, 1),
    ('任天堂',             0, 'GAMES', NULL, 0, FALSE, 0),
    -- ============ SPORTS ===================================================
    ('nike',              0, 'SPORTS', NULL, 0, FALSE, 1),
    ('耐克',               0, 'SPORTS', NULL, 0, FALSE, 0),
    ('adidas',            0, 'SPORTS', NULL, 0, FALSE, 1),
    ('阿迪达斯',           0, 'SPORTS', NULL, 0, FALSE, 0),
    -- ============ FOOD_BEV =================================================
    ('coca cola',         0, 'FOOD_BEV', NULL, 0, FALSE, 1),
    ('可口可乐',           0, 'FOOD_BEV', NULL, 0, FALSE, 0),
    ('pepsi',             0, 'FOOD_BEV', NULL, 0, FALSE, 1),
    ('百事可乐',           0, 'FOOD_BEV', NULL, 0, FALSE, 0),
    ('mcdonald',          0, 'FOOD_BEV', NULL, 0, FALSE, 1),
    ('麦当劳',             0, 'FOOD_BEV', NULL, 0, FALSE, 0),
    -- ============ RETAIL ===================================================
    ('walmart',           0, 'RETAIL', NULL, 0, FALSE, 1),
    ('沃尔玛',             0, 'RETAIL', NULL, 0, FALSE, 0),
    ('costco',            0, 'RETAIL', NULL, 0, FALSE, 1),
    ('开市客',             0, 'RETAIL', NULL, 0, FALSE, 0),
    -- ============ DISCRETIONARY — luxury / FMCG ============================
    ('lvmh',              0, 'DISCRETIONARY', NULL, 0, FALSE, 1),
    ('路威酩轩',           0, 'DISCRETIONARY', NULL, 0, FALSE, 0),
    ('hermes',            0, 'DISCRETIONARY', NULL, 0, FALSE, 1),
    ('爱马仕',             0, 'DISCRETIONARY', NULL, 0, FALSE, 0),
    ('宝洁',               0, 'DISCRETIONARY', NULL, 0, FALSE, 0),
    ('unilever',          0, 'DISCRETIONARY', NULL, 0, FALSE, 1),
    ('联合利华',           0, 'DISCRETIONARY', NULL, 0, FALSE, 0),
    -- ============ METALS — mining ==========================================
    ('bhp',               0, 'METALS', NULL, 0, FALSE, 1),
    ('必和必拓',           0, 'METALS', NULL, 0, FALSE, 0),
    ('rio tinto',         0, 'METALS', NULL, 0, FALSE, 1),
    ('vale',              0, 'METALS', NULL, 0, FALSE, 1),
    ('淡水河谷',           0, 'METALS', NULL, 0, FALSE, 0);
COMMIT;
