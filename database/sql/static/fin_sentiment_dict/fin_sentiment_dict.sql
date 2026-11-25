-- ============================================================================
--  Table: static.fin_sentiment_dict — the CFSD 中文金融情感词典
--  (seeded Chinese financial-sentiment reference vocabulary)
--
--  ONE row per word: a Chinese financial-sentiment word and its polarity.
--  Source: CFSD (姜富伟 / 孟令超 / 唐国豪) — 8,793 unique words (5,600
--  negative + 3,172 positive + 21 dual-sign 'both'), built by translating
--  the Loughran-McDonald master dictionary into Chinese and expanding it
--  with word2vec over Chinese financial corpora; validated on A-share
--  return prediction (媒体文本情绪与股票回报预测, 经济学(季刊) 2021年第4期,
--  1323-1344). Free for research with citation:
--    * Fuwei Jiang, Joshua Lee, Xiumin Martin, Guofu Zhou. "Manager
--      Sentiment and Stock Returns". Journal of Financial Economics
--      132(1), 2019, 126-149.
--    * 姜富伟，孟令超，唐国豪. 媒体文本情绪与股票回报预测.
--      经济学(季刊), 2021年第4期.
--  Downloaded from github.com/MengLingchao/Chinese_financial_sentiment_
--  dictionary (2026-11-21) into temp_scripts/cfsd_download/ (raw-download
--  quarantine); the seed files here are the vocabulary's source of record.
--
--  STATIC seed layer: rows land ONLY via the seed files here
--  (01_seed_negative / 02_seed_positive / 03_seed_both — each re-applies
--  by DELETE of its polarity slice + INSERT; run each whole file in ONE
--  transaction). Never hand-UPDATE a row; edit the owning seed file and
--  re-apply.
--
--  Study intent (2026-11-21): the lexicon half of the news-text
--  quantification proposals — a deterministic polarity overlay and
--  cross-check on the LLM article sentiment (text.news_digestions
--  sentiment_level), scored the same way news_keywords counts taxonomy
--  keywords (substring / exact-token, negation-aware). Consumers to come:
--  builds.text keyword scoring and the analysis daily-sentiment studies.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS static;

CREATE TABLE IF NOT EXISTS static.fin_sentiment_dict (
    word     TEXT     NOT NULL,  -- the dictionary word (CJK/alnum; whitespace-stripped from the source xlsx)
    polarity SMALLINT NOT NULL,  -- label code (static.label_dim 'fin_dict_polarity'): 0=negative 1=positive 2=both
    CONSTRAINT pk_fin_sentiment_dict PRIMARY KEY (word)
);

COMMENT ON TABLE static.fin_sentiment_dict IS
  'CFSD 中文金融情感词典 — the Chinese financial-sentiment dictionary '
  '(姜富伟 / 孟令超 / 唐国豪): 8,793 unique words, LM-translate + '
  'word2vec-expand construction, A-share validated. One row per word '
  'with its polarity; matched against article text by consumers like the '
  'keyword_maps taxonomy (substring for CJK, whole-word for ASCII) to '
  'build deterministic lexicon sentiment overlays and cross-checks on '
  'the LLM digestion scores. Source of record: the seed files under '
  'database/sql/static/fin_sentiment_dict/ (01..03, re-appliable by '
  'per-slice DELETE + INSERT).';

COMMENT ON COLUMN static.fin_sentiment_dict.word IS
  'The dictionary word — cleaned of the source xlsx''s whitespace/BOM '
  'artifacts, one polarity per word (PK). All 8,793 entries are CJK or '
  'alphanumeric. Matched against article text by the consumers using the '
  'static.keyword_maps conventions (plain substring for CJK, whole-word '
  'case-insensitive for ASCII).';

COMMENT ON COLUMN static.fin_sentiment_dict.polarity IS
  'The word''s polarity, as its static.label_dim (''fin_dict_polarity'') '
  'SMALLINT code: 0=negative 1=positive 2=both. 2 = the 21 words the '
  'source lists in BOTH its positive and negative sheets (context-'
  'dependent sign, e.g. 冷静/大胆/揭示) — consumers weight them as they '
  'see fit instead of silently picking a side.';
