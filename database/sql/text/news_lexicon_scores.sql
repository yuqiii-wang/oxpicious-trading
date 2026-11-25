-- ============================================================================
--  Table: text.news_lexicon_scores — per-article CFSD lexicon sentiment
--  (the deterministic cross-check on the LLM digestion score)
--
--  ONE row per digested article: the CFSD 中文金融情感词典 dictionary
--  score of the article text (static.fin_sentiment_dict, unit-based
--  negation-aware scoring — the CFSD paper's own rule, 姜富伟/孟令超/
--  唐国豪 经济学(季刊) 2021), alongside the disagreement flag against
--  the article's LLM digestion score (text.news_digestions.sentiment_level).
--  Written ONLY by analysis.news_lexicon (incremental by missing news_id;
--  --force truncates + rescoring everything). The daily blend
--  (analysis.news_text_daily) reads lex_score and disagrees: a disagreeing
--  article (sign mismatch AND |llm - lex| >= 2) is down-weighted 0.5x in
--  every daily aggregate.
--
--  STATIC-ish provenance: dict_version pins the dictionary seed state
--  (the static/fin_sentiment_dict git state) and engine_version pins the
--  scorer code — a quarterly recalibration bumps engine_version and
--  re-scores (--force), never hand-UPDATEs a row.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Per-article deterministic sentiment score from the CFSD Chinese financial dictionary with its disagreement flag against the LLM digestion score.
-- tags: sentiment lexicon, 情感词典, CFSD, 中文金融情感词典, news text, 新闻文本, sentiment score, 情感分数, negation, 否定, cross-check, 交叉验证, reference, 参考


CREATE TABLE IF NOT EXISTS text.news_lexicon_scores (
    news_id        BIGINT       NOT NULL,  -- FK text.news.news_id (one row per digested article)
    lex_score      DOUBLE PRECISION NOT NULL,  -- CFSD unit score rescaled onto [-5,+5] (clip)
    n_units        INTEGER      NOT NULL,  -- sentiment words (units) counted in the text
    neg_flips      INTEGER      NOT NULL,  -- negation flips applied (unit negator count, summed)
    degree_adj     INTEGER      NOT NULL,  -- articles' unit contributions carrying a degree-adverb multiplier
    disagrees      BOOLEAN      NOT NULL,  -- sign(lex) != sign(llm) AND |llm - lex| >= 2 — down-weight 0.5x in the daily blend
    llm_sentiment  DOUBLE PRECISION NOT NULL,  -- the digestion score at scoring time (text.news_digestions.sentiment_level)
    dict_version   TEXT         NOT NULL,  -- dictionary seed state pin ('cfsd-2026-11-21')
    engine_version TEXT         NOT NULL,  -- scorer code version (bumped on recalibration)
    CONSTRAINT pk_news_lexicon_scores PRIMARY KEY (news_id)
);

CREATE INDEX IF NOT EXISTS idx_news_lexicon_scores_disagrees
    ON text.news_lexicon_scores (disagrees)
    WHERE disagrees;

COMMENT ON TABLE text.news_lexicon_scores IS
  'CFSD lexicon sentiment per digested article — the deterministic '
  'dictionary score (unit-based, negation-aware, degree-adverb tiers; '
  'the CFSD paper 姜富伟等 2021 rule over static.fin_sentiment_dict) '
  'paired with the disagreement verdict against the article''s LLM '
  'digestion score. Disagreeing articles carry half weight in the '
  'daily news-text aggregates (text.news_text_daily). Source of '
  'record: the analysis.news_lexicon pipeline — rows land only via '
  'its upserts (dict_version/engine_version pin the inputs; a '
  'recalibration re-scores --force, never hand-UPDATEs).';

COMMENT ON COLUMN text.news_lexicon_scores.news_id IS
  'The digested article (text.news.news_id); one row per article, '
  'scored from title + body with the StructBERT segmentation the '
  'corpus itself uses.';

COMMENT ON COLUMN text.news_lexicon_scores.lex_score IS
  'The CFSD unit score rescaled onto the digestion score''s [-5,+5] '
  'range: raw = SUM over sentiment units of (-1)^negators * polarity '
  '* degree_multiplier / n_units (both-polarity words count 0.5 '
  'either sign); lex_score = clip(raw * 5, -5, +5). NULL never.';

COMMENT ON COLUMN text.news_lexicon_scores.disagrees IS
  'The cross-check verdict: TRUE when the lexicon and the LLM '
  'disagree on sign AND by at least 2 points on the [-5,+5] scale — '
  'the ambiguity flag that halves the article''s weight in the daily '
  'blend. Quarterly audits review TRUE rows and adjust thresholds '
  'only from measured error.';

COMMENT ON COLUMN text.news_lexicon_scores.llm_sentiment IS
  'Snapshot of text.news_digestions.sentiment_level at scoring time '
  '— the comparison side of disagrees; the live digestion row stays '
  'authoritative (a re-digested article re-scores on its next '
  'lexicon pass only when the pipeline sees it as missing, so a '
  'forced re-digestion should be followed by --force here).';

COMMENT ON COLUMN text.news_lexicon_scores.dict_version IS
  'The dictionary state pin (static.fin_sentiment_dict seed set, '
  '''cfsd-2026-11-21'' at seeding). A dictionary edit re-scores '
  '--force with the new pin.';

COMMENT ON COLUMN text.news_lexicon_scores.engine_version IS
  'The scorer code version (negator/degree vocabularies, unit '
  'rules). Quarterly recalibration bumps this and re-scores.';
