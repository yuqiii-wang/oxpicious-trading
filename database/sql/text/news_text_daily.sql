-- ============================================================================
--  Table: text.news_text_daily — the daily news-text metric panel
--  (sentiment state + flow flags per scope, the assistant-info layer)
--
--  ONE row per (date, scope): the daily news-text metrics the 2026-11-21
--  feasibility study validated on the repo's own corpus. Scopes
--  (static.label_dim 'news_scope_kind'):
--    0 market_all   — every digested NON-FAKE article that day
--                     (fake-news verdicts on text.news_digestions
--                     excluded — 2026-11-22), aligned to the
--                     SSE Composite (000001) trading calendar;
--    1 market_recap — the BROAD_SSE-tagged broad-market recap subset,
--                     the same calendar;
--    2 industry     — per-industry EVENT-DAY rows (scope_code =
--                     industry_id; a row exists only on days the
--                     industry is substantially CO-MENTIONED — the
--                     2026-11-22 attribution rework: an article
--                     attributes to every catalog industry whose
--                     keywords it matches with >= 4 total matched
--                     chars, not just its single primary tag, so
--                     niche industries like 高端制造/ADVMFG populate
--                     from the same corpus; the daily-index grain
--                     stays infeasible, the validated event-study
--                     framing instead);
--    3 sector       — per-sector EVENT-DAY rows (scope_code = the L1
--                     parent sector_id, the classification catalog's
--                     industry->sector map): the same co-mention
--                     event-day aggregates rolled UP the taxonomy —
--                     one article counts once per sector however many
--                     member industries it mentions — a sector pools
--                     its member industries' coverage, so its event
--                     days run denser. BROAD_SSE mentions count
--                     toward the BROAD sector (their catalog parent;
--                     recap PRIMARIES are excluded upstream).
--  Market-kind rows carry the full metric set (trailing z / ewm /
--  abnormal-flow); industry- and sector-kind rows carry the event-day
--  aggregates only (levels + counts, the curve columns NULL,
--  low_coverage true unless >= 3 scored articles).
--
--  Validated reading (2026-11-21/22 study, 2023+ window):
--    * sent_level / sent_z have NO short-horizon directional alpha —
--      state/context, never a direction call;
--    * sent_ema10 is CONTRARIAN at the monthly tail (after top-20%
--      sentiment months +0.33% vs after bottom-20% +1.54% next month);
--    * flow_af is the most consistent risk flag (negative forward IC
--      at 1-10d in every scope/year).
--  Forward returns and regime flags are deliberately NOT stored —
--  derive them by joining stats.v_index_daily on (scope_code, date).
--
--  Writer: analysis.news_text_daily (incremental by missing date with a
--  15-day grace tail; --force truncates + full rebuild). ALL INSERTs
--  are in Python — no INSERT SQL in this file.
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each day's news-text metric panel per scope — market sentiment state (level, trailing z, ewm halflife 3/10), abnormal news flow flag, and per-industry / per-sector event-day aggregates.
-- tags: news sentiment, 新闻情绪, market sentiment, 市场情绪, news flow, 新闻流量, abnormal coverage, 异常报道量, text metrics, 文本指标, sentiment index, 情绪指数, sector sentiment, 板块情绪, contrarian, 反向, reference, 参考


CREATE TABLE IF NOT EXISTS text.news_text_daily (
    date         DATE                   NOT NULL,  -- trading day the metrics speak for (weekend articles roll to the next trading day)
    scope_kind   SMALLINT               NOT NULL,  -- label code (static.label_dim 'news_scope_kind'): 0=market_all 1=market_recap 2=industry 3=sector
    scope_code   TEXT                   NOT NULL,  -- kind 0/1: the SSE Composite index code '000001'; kind 2: the industry_id (stats.sec_classification); kind 3: the L1 sector_id (text.news.sector_id)
    n_articles   INTEGER                NOT NULL,  -- articles in scope that day (market kinds: all, incl. undigested/fake — the coverage basis flow_af reads; industry/sector kinds: the scored co-mention count)
    n_scored     INTEGER                NOT NULL,  -- digested non-fake articles entering the sentiment aggregates
    sent_level   DOUBLE PRECISION,                 -- lexicon-blended mean sentiment [-5,+5]; NULL only when n_scored = 0 (a day with no scored articles)
    sent_z       DOUBLE PRECISION,                 -- (level - trailing-60d mean)/std, min 10 obs — market kinds only
    sent_ema3    DOUBLE PRECISION,                 -- ewm(sent_z, halflife 3, ignore_na) — the shock line
    sent_ema10   DOUBLE PRECISION,                 -- ewm(sent_z, halflife 10, ignore_na) — the persistent line (validated contrarian tail)
    sent_delta   DOUBLE PRECISION,                 -- level day-over-day change (the CFSD paper's "change" predictor)
    flow_af      DOUBLE PRECISION,                 -- (n_articles - trailing-63d median)/IQR, min 15 obs — the validated abnormal-coverage risk flag
    flow_pos     INTEGER                NOT NULL DEFAULT 0,  -- articles blended-scored > +1
    flow_neg     INTEGER                NOT NULL DEFAULT 0,  -- articles blended-scored < -1 (a flag, never bucketed)
    disp_origin  DOUBLE PRECISION,                 -- std across per-origin mean scores that day; NULL when < 2 origins covered the scope
    disp_z       DOUBLE PRECISION,                 -- (disp_origin - trailing-60d mean)/std, moments SHIFTED 1 row, min 20 obs — market kinds only (2026-11-22: the measured dispersion-confirmation axis)
    lex_tone_z   DOUBLE PRECISION,                 -- px-tone keyword z (kw_market up-family minus down-family mentions, trailing-60d z) — market kinds only
    low_coverage BOOLEAN                NOT NULL DEFAULT false,  -- TRUE when n_scored < 3 — UI renders the day greyed, never as a reading
    CONSTRAINT pk_news_text_daily PRIMARY KEY (date, scope_kind, scope_code)
);

CREATE INDEX IF NOT EXISTS idx_news_text_daily_scope
    ON text.news_text_daily (scope_kind, scope_code, date);

COMMENT ON TABLE text.news_text_daily IS
  'The daily news-text metric panel — per (date, scope): market '
  'sentiment state (blended level, trailing z, ewm halflife 3/10, '
  'day change), the abnormal-news-flow risk flag, sign-split article '
  'counts, origin dispersion, and the lexicon tone cross-check; '
  'industry-kind rows are event-day aggregates (a row only on days '
  'the industry is substantially CO-MENTIONED — an article '
  'attributes to every catalog industry whose keywords it matches '
  'with >= 4 total matched chars, the 2026-11-22 attribution rework '
  'that lets niche industries populate), sector-kind rows the same '
  'aggregates rolled up the taxonomy to the L1 parent sector (one '
  'article once per sector). The 2026-11-21 feasibility study '
  'validated the panel on the repo''s own corpus: sentiment levels '
  'are STATE not direction, sent_ema10 is contrarian at the monthly '
  'tail, flow_af is the consistent negative-forward-IC risk flag. '
  'Source of record: the analysis.news_text_daily pipeline — rows '
  'land only via its upserts.';

COMMENT ON COLUMN text.news_text_daily.date IS
  'The trading day the row speaks for. Articles publish on calendar '
  'dates; a weekend/holiday article rolls FORWARD to the next SSE '
  'trading day (its information is first tradable there — T+1 '
  'compatible). Industry rows keep the same roll.';

COMMENT ON COLUMN text.news_text_daily.scope_kind IS
  'The scope family, as its static.label_dim (''news_scope_kind'') '
  'SMALLINT code: 0=market_all (every digested NON-FAKE article that '
  'day vs the SSE Composite), 1=market_recap (the BROAD_SSE-tagged recap '
  'subset, the source class split the CFSD paper keeps), 2=industry '
  '(event-day rows per industry_id — substantial keyword '
  'co-mentions), 3=sector (event-day rows per L1 sector_id — the '
  'co-mention aggregates rolled up the taxonomy).';

COMMENT ON COLUMN text.news_text_daily.scope_code IS
  'The scope identity: ''000001'' (SSE Composite) for the market '
  'kinds — forward returns join stats.v_index_daily on it — the '
  'canonical industry_id (stats.sec_classification) for kind 2, or '
  'the L1 parent sector_id (the classification catalog''s '
  'industry->sector map, e.g. FIN/BROAD) for '
  'kind 3 (identity columns stay TEXT — the standing exception).';

COMMENT ON COLUMN text.news_text_daily.sent_level IS
  'The lexicon-blended mean sentiment: per article s = 0.7*LLM + '
  '0.3*lexicon (lexicon side absent -> LLM alone), a disagreeing '
  'article (text.news_lexicon_scores.disagrees) weighted 0.5. Computes '
  'from ONE scored article (LEVEL_MIN_SCORED — the 2026-11-22 fill '
  'study: the former n>=3 guard blanked a third of the recap scope); '
  'NULL only on a day with zero scored articles. Thin days carry the '
  'low_coverage flag (n_scored < 3) — UI greys them. STATE, not a '
  'direction call.';

COMMENT ON COLUMN text.news_text_daily.sent_ema10 IS
  'The persistent sentiment line: ewm of sent_z with halflife 10 '
  'trading days (NaN days skipped). The validated CONTRARIAN tail — '
  'after top-20% ema10 months the index averaged +0.33% the next '
  'month vs +1.54% after bottom-20% months (2023+ study) — read it '
  'as stretched-mood context, never as a momentum signal.';

COMMENT ON COLUMN text.news_text_daily.disp_z IS
  'The origin-dispersion STATE, z-space: (disp_origin - trailing-60-'
  'trading-day mean)/std with the moments SHIFTED one row (the trigger '
  'day''s own dispersion never sets its own bar — unlike sent_z, whose '
  'state convention keeps the day in its own window; the interaction '
  'studies measured this form, min 20 obs). The 2026-11-22 improvement '
  'study''s strongest CONFIRMATION axis (Isakin-2023 disagreement): the '
  'sentiment shock with abnormally HIGH cross-origin disagreement '
  '(disp_z >= 1) carried +1.74% blend / excess +1.46pp / t=6.87 vs the '
  'shock alone at +0.59% — the sources fighting each other marks the '
  'washout, not dilutes it. Market kinds only.';

COMMENT ON COLUMN text.news_text_daily.flow_af IS
  'The abnormal-news-flow flag: (n_articles - trailing-63-trading-day '
  'median) / IQR of the same window (min 40 obs). Validated as the '
  'panel''s most consistent risk finding: high abnormal coverage '
  'precedes LOWER forward returns at 1-10d horizons in every scope '
  'and year studied (2023+, IC -0.05 .. -0.08).';

COMMENT ON COLUMN text.news_text_daily.low_coverage IS
  'TRUE when n_scored < 3 — the day''s reading is THIN (computed but '
  'noisy; the level no longer blanks on it, the 2026-11-22 fill '
  'study). Consumers render these days greyed.';
