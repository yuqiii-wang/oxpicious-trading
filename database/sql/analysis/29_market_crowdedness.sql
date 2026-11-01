-- ============================================================================
--  Market Crowdedness — per-(sec_type, code, date) crowdedness curve for
--  indices and composite industries.
--
--    Table: analysis.market_crowdedness
--    PK: (code, sec_type, date)
--
--  Serves the Commons "Market Crowdedness" page
--  (/analysis/commons/market-crowdedness) through /api/analysis/
--  market-crowdedness/* (themes / codes / chart). The UI nav is the
--  FULL classification universe (stats.sec_classification type='index'
--  + stats.industry_identity entities) — the nav is NOT restricted to
--  codes that already have rows, so codes outside the pipeline scope
--  still plot their close/amount (metric stays NULL).
--
--  POPULATION
--    analysis.market_crowdedness (Python module). Both sides cover the
--    FULL universe: index side = all active classified indexes
--    (stats.sec_classification type='index', is_active, composition-
--    backed — explicitly authorized 2026-10-31; was the 10 study
--    indices during the initial implementation); industry side = the
--    full composite-industry universe. Per project rule,
--    ALL INSERTs are in Python — no raw INSERT...SELECT SQL in this
--    file.
--
--    sec_type ∈ ('index' | 'industry') — the only sec_types the UI
--    exposes and the pipeline is scoped to (the shared label_dim
--    'sec_type' domain is 0=etf 1=index 2=stock 3=industry; the CHECK
--    keeps the full domain so the column decodes like every other
--    analysis sec_type column).
--
--  METRIC DEFINITION (v2, 2026-10-31 — percentile-normalized multi-
--  pillar score; v1 was 0.5*concentration + 0.5*flow_change on RAW
--  levels, which conflated a pool's STRUCTURAL top-heaviness with
--  speculative crowding and carried no valuation / co-movement
--  information)
--    Pool per code (LATEST-SNAPSHOT house convention — the current
--    composition is the proxy for historical membership):
--      index    — latest stats.sec_composition snapshot
--                 (source_type='index') of that index;
--      industry — DISTINCT union of member stocks across the industry's
--                 member indices (stats.sec_classification type='index',
--                 is_active, is_industry_not_strategy).
--    On each date t the ACTIVE pool A(t) = member stocks with
--    trading_amount > 0 that day (stats.stock_liquidity_margin, yuan;
--    suspended / placeholder-zero stocks drop out of both numerator and
--    denominator). The HOT set H(t) = the top 10% of A(t) by the
--    stock's 60-ACTIVE-DAY MEAN trading amount as of t (floored at 5
--    names, |H(t)| = max(ceil(0.1·|A(t)|), 5); a stock needs ≥ 20
--    active days to be rankable — flash names cannot enter). The MA60
--    basis replaced the naive day-amount ranking 2026-11-01 (study on
--    the 10 study indices: the day-amount list churned 17-29% of its
--    names EVERY DAY vs 1-2% for the MA60 list — the day ranking was
--    noise, not information).
--
--    Four RAW pillars per (pool, date), each an evidence column:
--    The turnover pillars read the 5-ACTIVE-DAY MEAN amount
--    (PILLAR basis — smoothing study 2026-11-01: single-day Δamt drove
--    63% of day-over-day score movement; the ma5 basis cuts score noise
--    43% and keeps 88% of event peaks):
--      concentration(t) = Σ_{H(t)} ma5 / Σ_{A(t)} ma5
--                         (how much of the pool's 5-day-mean trading
--                         sits in the STABLE hot names)              0..1
--      flow_change(t)   = Σ_{H(t)} max(Δma5, 0) / Σ_{A(t)} max(Δma5, 0)
--                         with Δma5 = ma5(t) − ma5(previous traded day)
--                         (the gross INCREASE of the 5-day mean —
--                         sustained multi-day inflow into the hot
--                         names, not one-day spikes)                  0..1
--      comovement(t)    = mean over H(t) of corr120(r_stock, R_pool∖i):
--                         the trailing-120-active-day Pearson
--                         correlation between the stock's daily return
--                         (pct_change/100, falling back to
--                         close/prev_close − 1 where the source leaves
--                         pct_change NULL — the SSE archive) and the
--                         pool's leave-one-out equal-weight return
--                         of the day's active members (≥30 joint-valid
--                         return obs, positive variance; else the stock
--                         contributes no value — 120 rows not 60 because
--                         pct_change covers only ~60-75% of member-days).
--                         The co-movement pillar: crowded pools trade as
--                         one bloc.                                      −1..1
--      valuation(t)     = mean over H(t) of the stock's pe
--                         cross-sectional percentile within A(t)
--                         (stats.stock_basic_stats.pe; NULL pe =
--                         loss-making / no data, excluded from the
--                         rank). The valuation-spread pillar: hot names
--                         bid above the pool's typical name. 0.5 = at
--                         the pool median.                            0..1
--
--    Turnover-concentration EVIDENCE (added 2026-11-01 per the
--    concentration study; NOT score inputs — gini / effective-N carry
--    the same signal as the top-k shares, |ρ| ≈ 0.92-0.94):
--      top1_share / top5_share / top10_share(t) = the top 1% / 5% / 10%
--          of A(t) by DAY amount (min 1 name) and their share of the
--          day's pool amount — the classic 换手集中度 curve family    0..1
--      effective_n(t) = (Σ amt)² / Σ amt² — the effective traded-name
--          COUNT (inverse Herfindahl; 1 = one name takes everything,
--          |A(t)| = perfectly equal). Robust to head-list churn.  1..|A|
--
--    PERCENTILE NORMALIZATION (the v2 core): each pillar is read
--    against the pool's OWN history — x_pct(t) = the share of non-NULL
--    pillar values in the trailing 250 pool-date rows (≤ t) that are
--    ≤ x(t), requiring ≥ 60 non-NULL observations in the window. A
--    structurally top-heavy pool no longer scores crowded on quiet
--    days: only moves toward the pool's own extremes do.
--
--    crowdedness_score(t) = renormalized weight blend of the four
--    percentile pillars,
--        0.30·concentration_pct + 0.30·flow_pct
--      + 0.25·comovement_pct  + 0.15·valuation_pct
--      over the pillars defined on the date (the turnover pillars are
--      required; comovement / valuation drop out — with their weights —
--      on dates where the raw pillar is undefined).
--    All *_pct columns and the score are 0..1, higher = more crowded.
--    Rows exist only where the value is defined (active pool ≥ 10 —
--    below it the floored hot set would cover half the pool or more;
--    concentration_pct / flow_pct additionally need the 60-observation
--    percentile history) — NULL / uncomputable days have NO row (the
--    analysis.pe invalid-value rule). The percentile warm-up drops
--    each pool's first ~quarter-year of defined days.
--
--  ============================================================================

CREATE TABLE IF NOT EXISTS analysis.market_crowdedness (
    sec_type          SMALLINT      NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock 3=industry
    code              TEXT          NOT NULL,
    date              DATE          NOT NULL,

    crowdedness_score NUMERIC(10,4) NOT NULL,  -- renormalized weight blend of the four *_pct pillars (percentiles vs the pool's own 250-day history)
    concentration     NUMERIC(10,4) NOT NULL,  -- RAW level pillar: the STABLE hot set's (top 10% by 60-active-day mean amount) share of the day's pool amount
    flow_change       NUMERIC(10,4) NOT NULL,  -- RAW flow pillar: the stable hot set's share of the pool's gross amount increase
    comovement        NUMERIC(10,4),           -- RAW co-movement pillar: mean corr120(hot stock return, LOO pool return), -1..1; NULL when no hot member has a defined correlation
    valuation         NUMERIC(10,4),           -- RAW valuation pillar: mean hot pe cross-sectional percentile within the pool, 0..1; NULL when no hot member has a valid pe
    top1_share        NUMERIC(10,4) NOT NULL,  -- turnover-concentration evidence: top 1% of the active pool by DAY amount (min 1 name), share of the day's pool amount
    top5_share        NUMERIC(10,4) NOT NULL,  -- same for the top 5%
    top10_share       NUMERIC(10,4) NOT NULL,  -- same for the top 10%
    effective_n       NUMERIC(10,4) NOT NULL,  -- turnover-concentration evidence: effective traded-name count (Σamt)²/Σamt² (inverse Herfindahl, 1..active_n)
    concentration_pct NUMERIC(10,4) NOT NULL,  -- concentration vs the pool's own trailing 250-day history (percentile, 0..1)
    flow_pct          NUMERIC(10,4) NOT NULL,  -- flow_change vs the pool's own trailing 250-day history (percentile, 0..1)
    comovement_pct    NUMERIC(10,4),           -- comovement vs the pool's own trailing 250-day history (percentile, 0..1); NULL where comovement is NULL
    valuation_pct     NUMERIC(10,4),           -- valuation vs the pool's own trailing 250-day history (percentile, 0..1); NULL where valuation is NULL
    top_stocks        TEXT          NOT NULL,  -- comma-joined names of the hot set (ma60-amount order)

    CONSTRAINT pk_market_crowdedness PRIMARY KEY (code, sec_type, date),
    CONSTRAINT chk_market_crowdedness_sec_type
        CHECK (sec_type IN (0, 1, 2, 3))  -- label codes: 0=etf 1=index 2=stock 3=industry
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'market_crowdedness', 16);

CREATE INDEX IF NOT EXISTS idx_market_crowdedness_date
    ON analysis.market_crowdedness (date);
CREATE INDEX IF NOT EXISTS idx_market_crowdedness_sec_type_date
    ON analysis.market_crowdedness (sec_type, date);

COMMENT ON TABLE  analysis.market_crowdedness IS 'Per-(code, sec_type, date) market crowdedness curve for indexes (sec_type=1, all active classified indexes) and composite industries (sec_type=3, code = stats.sec_classification.industry_id). v2 percentile-normalized multi-pillar score: crowdedness_score = renormalized blend (0.30/0.30/0.25/0.15) of the concentration / flow_change / comovement / valuation pillars, each read as its percentile vs the pool''s own trailing 250-day history; the raw pillars stay as evidence columns (see 29_market_crowdedness.sql header for the full definition). Serves the Commons Market Crowdedness page via /api/analysis/market-crowdedness/chart. Built by analysis.market_crowdedness; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.market_crowdedness.sec_type          IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf, 1 = index (CSI-style index), 2 = stock, 3 = industry (composite industry, code = stats.sec_classification.industry_id). This pipeline is scoped to 1 (index) and 3 (industry).';
COMMENT ON COLUMN analysis.market_crowdedness.code              IS 'Security code (bare index code e.g. 000001; sec_type=3: industry_id e.g. BANKS).';
COMMENT ON COLUMN analysis.market_crowdedness.date              IS 'Trading date.';
COMMENT ON COLUMN analysis.market_crowdedness.crowdedness_score IS 'Market crowdedness (0..1, higher = more crowded): renormalized weight blend 0.30*concentration_pct + 0.30*flow_pct + 0.25*comovement_pct + 0.15*valuation_pct over the pillars defined on the date (turnover pillars required). Each *_pct is the pillar''s percentile vs the pool''s own trailing-250-pool-date-row history (>= 60 non-NULL obs). Blended from UNROUNDED percentiles, stored rounded to 4dp. Rows exist only where the value is defined — NULL / uncomputable days have NO row.';
COMMENT ON COLUMN analysis.market_crowdedness.concentration     IS 'RAW level pillar (0..1): the STABLE hot set''s share of the pool''s 5-ACTIVE-DAY MEAN trading amount — hot set = top 10% of the active pool by the 60-active-day MEAN amount (min 5 names, >= 20 active days to rank; the day-amount ranking churned 17-29% of names daily vs 1-2% here, study 2026-11-01). The ma5 basis cuts score noise 43% (smoothing study 2026-11-01).';
COMMENT ON COLUMN analysis.market_crowdedness.flow_change       IS 'RAW flow pillar (0..1): the stable hot set''s share of the pool''s GROSS increase of the 5-active-day MEAN amount (Δma5 = ma5(t) − ma5(previous traded day), positive part only) — sustained multi-day inflow, not one-day spikes. No row when the pool''s gross increase is zero.';
COMMENT ON COLUMN analysis.market_crowdedness.comovement        IS 'RAW co-movement pillar (−1..1, higher = the hot names move more in lockstep with the pool): mean over the hot set of the trailing-120-active-day Pearson correlation between the stock''s daily return (pct_change/100, falling back to close/prev_close − 1 where the source leaves pct_change NULL — the SSE archive) and the pool''s leave-one-out equal-weight return of the day''s active members (>= 30 joint-valid obs, positive variance). NULL when no hot member has a defined correlation. Added in v2 (2026-10-31).';
COMMENT ON COLUMN analysis.market_crowdedness.valuation         IS 'RAW valuation pillar (0..1, 0.5 = hot names priced at the pool median): mean over the hot set of each stock''s pe cross-sectional percentile within the day''s active pool (stats.stock_basic_stats.pe; NULL pe = loss-making / no data, excluded from the rank). NULL when no hot member has a valid pe. Added in v2 (2026-10-31).';
COMMENT ON COLUMN analysis.market_crowdedness.concentration_pct IS 'concentration read against the pool''s own history (0..1): share of non-NULL concentration values in the trailing 250 pool-date rows that are <= the day''s value (>= 60 non-NULL obs required). NOT NULL — a required score pillar.';
COMMENT ON COLUMN analysis.market_crowdedness.flow_pct          IS 'flow_change read against the pool''s own history (0..1): share of non-NULL flow_change values in the trailing 250 pool-date rows that are <= the day''s value (>= 60 non-NULL obs required). NOT NULL — a required score pillar.';
COMMENT ON COLUMN analysis.market_crowdedness.comovement_pct    IS 'comovement read against the pool''s own history (0..1): share of non-NULL comovement values in the trailing 250 pool-date rows that are <= the day''s value (>= 60 non-NULL obs required). NULL where comovement is NULL (the pillar drops out of the score blend with its weight).';
COMMENT ON COLUMN analysis.market_crowdedness.valuation_pct     IS 'valuation read against the pool''s own history (0..1): share of non-NULL valuation values in the trailing 250 pool-date rows that are <= the day''s value (>= 60 non-NULL obs required). NULL where valuation is NULL (the pillar drops out of the score blend with its weight).';
COMMENT ON COLUMN analysis.market_crowdedness.top1_share        IS 'Turnover-concentration evidence (0..1, not a score input): share of the day''s pool trading amount captured by the top 1% of the active pool by DAY amount (min 1 name). The classic 换手集中度 head cut — noisier than the ma60-based pillars by design (it is the raw daily ranking).';
COMMENT ON COLUMN analysis.market_crowdedness.top5_share        IS 'Turnover-concentration evidence (0..1, not a score input): top 5% by DAY amount, share of the day''s pool amount.';
COMMENT ON COLUMN analysis.market_crowdedness.top10_share       IS 'Turnover-concentration evidence (0..1, not a score input): top 10% by DAY amount, share of the day''s pool amount. Equals the naive pre-2026-11-01 concentration definition — kept as evidence.';
COMMENT ON COLUMN analysis.market_crowdedness.effective_n       IS 'Turnover-concentration evidence (1..active_n, not a score input): effective traded-name count (Σ amt)² / Σ amt² (inverse Herfindahl). 1 = one name takes everything; active_n = perfectly equal distribution. Robust to head-list churn (study 2026-11-01).';
COMMENT ON COLUMN analysis.market_crowdedness.top_stocks        IS 'Evidence for tooltips: comma-joined display names of the date''s hot set (top 10% of the active pool by 60-active-day MEAN amount, min 5 names, ma60 order). Free-form TEXT (open vocabulary), not a label_dim domain.';
