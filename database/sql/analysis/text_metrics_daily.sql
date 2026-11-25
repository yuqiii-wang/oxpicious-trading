-- ============================================================================
--  Daily text metrics — sentiment pressure + news flow per industry & market
--  Table: analysis.text_metrics_daily — one row per (industry_id, date).
--  industry_id '' = the broad-market row: ALL digestible articles that day
--  (tagged AND untagged). NOTE the different meaning of '' here vs
--  text.news_content_audit ('' = untagged bucket there): the market row is
--  the UNION of every industry row's scope plus the untagged articles.
--
--  Populated by analysis.text_metrics (DataAnalysis pipeline; registered in
--  analysis_identity). Nothing else writes or refreshes this table.
--  Rows exist only where defined: an (industry, day) with fewer than
--  MIN_ARTICLES digestible articles (config knob, default 3) has NO row —
--  the analysis.pe NULL rule (no row for uncomputable days). The trailing
--  z-scores are NULL during the 60-day warm-up edge.
--
--  Metrics follow the news-text quantification literature: sentiment_z is
--  Tetlock's (2007) abnormal-sentiment transform (deviation from the
--  trailing mean, NOT the raw level — the scorer's positive level shift
--  must not leak into the metric); flow_z is the abnormal news-volume
--  z-score (Heston-Sinha 2017 — flow carries the longer-horizon signal);
--  sentiment_std is the cross-source dispersion metric (Isakin 2023);
--  kw_tone is the closed-taxonomy lexicon overlay (bullish vs bearish
--  kw_market mentions) cross-checking the LLM sentiment. The neutral band
--  for pos/neg counts (|level| >= 0.5) matches the digestion summary's
--  direction-label band.
--
--  NOT hash-partitioned: ~35 industries x ~1,650 trading days ≈ 60k rows —
--  text-corpus scale, not per-code daily volume (the partition rule gates
--  on 10^6+ per-code rows).
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Daily per-industry and broad-market news-text metrics — abnormal sentiment z-score, sentiment dispersion, positive/negative news-flow counts, abnormal flow z-score, and the bullish/bearish keyword tone overlay.
-- tags: sentiment, 情绪, news flow, 新闻流量, abnormal sentiment, 异常情绪, dispersion, 分歧, keyword tone, 关键词语气, text metrics, 文本指标

CREATE TABLE IF NOT EXISTS analysis.text_metrics_daily (
    industry_id      TEXT              NOT NULL,  -- '' = broad market (all articles)
    date             DATE              NOT NULL,  -- article publish date (text.news date)
    article_count    INTEGER           NOT NULL,  -- digestible articles in scope that day
    digested_count   INTEGER           NOT NULL,  -- of them, with a sentiment_level (coverage evidence)
    sentiment_mean   DOUBLE PRECISION,            -- raw mean sentiment_level (skewed — prefer sentiment_z)
    sentiment_std    DOUBLE PRECISION,            -- cross-article std of sentiment_level (dispersion)
    sentiment_z      DOUBLE PRECISION,            -- (mean − trailing60d_mean) / trailing60d_std, prior days only
    sentiment_ma5    DOUBLE PRECISION,            -- sentiment_z smoothed over 5 days (incl. current)
    pos_count        INTEGER           NOT NULL DEFAULT 0,  -- articles with sentiment_level >= +0.5
    neg_count        INTEGER           NOT NULL DEFAULT 0,  -- articles with sentiment_level <= -0.5
    flow_z           DOUBLE PRECISION,            -- (article_count − trailing60d_mean) / trailing60d_std, prior days only
    kw_bull_mentions INTEGER           NOT NULL DEFAULT 0,  -- bullish kw_market mentions that day (audit matview)
    kw_bear_mentions INTEGER           NOT NULL DEFAULT 0,  -- bearish kw_market mentions that day
    kw_tone          DOUBLE PRECISION,            -- (bull − bear) / (bull + bear), NULL when both are 0
    created_at       TIMESTAMPTZ       NOT NULL DEFAULT now(),

    CONSTRAINT pk_text_metrics_daily PRIMARY KEY (industry_id, date),
    CONSTRAINT ck_text_metrics_daily_counts
        CHECK (article_count >= 0 AND digested_count >= 0 AND
               pos_count >= 0 AND neg_count >= 0 AND
               kw_bull_mentions >= 0 AND kw_bear_mentions >= 0),
    CONSTRAINT ck_text_metrics_daily_flow
        CHECK (pos_count + neg_count <= article_count)
);

-- Per-day cross-sectional reads (the market-wide '' rows scan via the PK;
-- this index serves whole-day sweeps and the pipeline's PK diff).
CREATE INDEX IF NOT EXISTS ix_text_metrics_daily_date
    ON analysis.text_metrics_daily (date DESC);

COMMENT ON TABLE analysis.text_metrics_daily IS
  'Daily news-text metrics per industry and for the broad market '
  '(industry_id '' = all articles): abnormal sentiment (trailing z), '
  'sentiment dispersion, positive/negative flow counts with the abnormal '
  'flow z-score, and the kw_market bullish/bearish tone overlay. '
  'Populated by analysis.text_metrics — sentiment_level components come '
  'from text.news_digestions, keyword mentions from '
  'text.news_keyword_audit. Nothing else writes it.';

COMMENT ON COLUMN analysis.text_metrics_daily.industry_id IS
  'The text.news industry tag; '''' = the broad-market row (every '
  'digestible article that day, tagged or not). Not the same convention '
  'as text.news_content_audit''s '''' untagged bucket — see the table '
  'banner.';

COMMENT ON COLUMN analysis.text_metrics_daily.article_count IS
  'Digestible articles in scope that day (rows with content, per the '
  'digestion work queue''s definition). The flow volume — flow_z '
  'normalizes it against the trailing window.';

COMMENT ON COLUMN analysis.text_metrics_daily.digested_count IS
  'How many of article_count carry a text.news_digestions.sentiment_level '
  '— the coverage/quality evidence for the row (a low ratio makes the '
  'sentiment columns noisy).';

COMMENT ON COLUMN analysis.text_metrics_daily.sentiment_mean IS
  'Raw mean of sentiment_level over the day''s digested articles. Keep '
  'for reference only — the scorer has a positive level shift (+0.62 '
  'mean over the 2020-2026 corpus); sentiment_z is the comparable '
  'series.';

COMMENT ON COLUMN analysis.text_metrics_daily.sentiment_std IS
  'Cross-article standard deviation of sentiment_level that day — the '
  'sentiment-dispersion / disagreement metric (best practice: dispersion '
  'is its own signal, not folded into the tone).';

COMMENT ON COLUMN analysis.text_metrics_daily.sentiment_z IS
  'Tetlock (2007) abnormal sentiment: (sentiment_mean − trailing 60-day '
  'mean) / trailing 60-day std, computed over PRIOR days only (the '
  'current day never enters its own window). NULL during the warm-up '
  'edge (fewer than the window''s worth of prior articles).';

COMMENT ON COLUMN analysis.text_metrics_daily.sentiment_ma5 IS
  'sentiment_z smoothed with a 5-day trailing mean including the current '
  'day (the market_crowdedness_score_ma5 smoothing pattern).';

COMMENT ON COLUMN analysis.text_metrics_daily.pos_count IS
  'Articles with sentiment_level >= +0.5 (the digestion direction-label '
  'band).';

COMMENT ON COLUMN analysis.text_metrics_daily.neg_count IS
  'Articles with sentiment_level <= -0.5 (the digestion direction-label '
  'band).';

COMMENT ON COLUMN analysis.text_metrics_daily.flow_z IS
  'Abnormal news volume: (article_count − trailing 60-day mean) / '
  'trailing 60-day std over PRIOR days only. Heston-Sinha (2017): news '
  'FLOW carries the longer-horizon return signal, stronger than tone at '
  'weekly+ horizons.';

COMMENT ON COLUMN analysis.text_metrics_daily.kw_bull_mentions IS
  'Sum of text.news_keyword_audit.mentions that day for the bullish '
  'kw_market codes (0 px_up, 2 sharp_up, 4 gap_up, 6 limit_up, 8 rebound, '
  '10 vol_up, 13 bullish, 18 break_up). Deterministic lexicon overlay on '
  'the LLM sentiment.';

COMMENT ON COLUMN analysis.text_metrics_daily.kw_bear_mentions IS
  'Sum of text.news_keyword_audit.mentions that day for the bearish '
  'kw_market codes (1 px_dn, 3 sharp_dn, 5 gap_dn, 7 limit_dn, 9 '
  'pullback, 11 vol_dn, 14 bearish, 19 break_dn). Non-directional codes '
  '(12 divergence, 15 volatile, 16 mood, 17 position) enter neither '
  'side.';

COMMENT ON COLUMN analysis.text_metrics_daily.kw_tone IS
  '(kw_bull_mentions − kw_bear_mentions) / (kw_bull_mentions + '
  'kw_bear_mentions) in [-1, 1]; NULL when both are 0. Disagreement with '
  'sentiment_z flags ambiguous days (the lexicon cross-check).';
