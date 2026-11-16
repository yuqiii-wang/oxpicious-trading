-- ============================================================================
--  Market Crowdedness, year-pinned hot sets — per-(sec_type, code,
--  hot_year, date) crowdedness variants with the hot basket FROZEN to
--  one year's hottest names.
--
--    Table: analysis.market_crowdedness_year
--    PK: (code, sec_type, hot_year, date)
--
--  WHY (2026-11-14): the rolling table re-selects its hot set every
--  trading date; even the stable ma60 list churns 1-2% of names daily
--  and the crowdedness_score inherits that membership noise. The pinned
--  variant freezes the basket so the curve reflects only price / flow /
--  co-movement / valuation moves of a FIXED set of names. Annual (not
--  quarterly) per the 2026-11-14 follow-up — the year is the pinning
--  grain.
--
--  Serves the Commons "Market Crowdedness" page
--  (/analysis/commons/market-crowdedness) through /api/analysis/
--  market-crowdedness/chart?hot_year=YYYY + /hot-years — the page
--  dropdown pins the hot set; the default (no param) keeps the rolling
--  analysis.market_crowdedness curve.
--
--  POPULATION
--    analysis.market_crowdedness (Python module — the pinned pass runs
--    in the same pipeline/chunk as the rolling pass, sharing every
--    hot-set-independent ingredient). Per project rule, ALL INSERTs are
--    in Python — no raw INSERT...SELECT SQL in this file.
--
--    sec_type ∈ ('index' | 'industry') — same scope as the rolling
--    table (the shared label_dim 'sec_type' domain is 0=etf 1=index
--    2=stock 3=industry; the CHECK keeps the full domain so the column
--    decodes like every other analysis sec_type column).
--
--  PINNED METRIC DEFINITION (2026-11-14; everything except the hot-set
--  rule is IDENTICAL to analysis.market_crowdedness v2 — see
--  market_crowdedness.sql for the full pillar/percentile/score spec):
--    Pool per code: same LATEST-SNAPSHOT membership as the rolling
--    table. Active pool A(t) = members with trading_amount > 0 on t.
--
--    YEAR HOT SET Y(y) for year y (hot_year = the calendar year):
--      |Y(y)| = max(ceil(0.1 · mean_{t ∈ y} |A(t)|), 5)
--      members = the top |Y(y)| of the pool by the member's MEAN
--      60-active-day amount (amt_ma60) over the year's OWN trading
--      days (the year-average ranking — most representative of "the
--      year's hottest names"; a member needs ≥ 20 active days for
--      amt_ma60 to be defined at all). The OPEN year uses
--      year-to-date and re-derives nightly; on year close the roster
--      freezes into analysis.market_crowdedness_hot_sets.
--
--    The pinned hot set applies to the pool's ENTIRE date span: a
--    chosen 2025 basket is hot from the pool's first computed date to
--    today. A pinned member contributes ONLY on dates it actually
--    traded — before its listing / after its exit it has no amount
--    rows and drops out of both numerator and denominator (the
--    requested not-yet-listed / already-exited masking, inherent to the
--    active-day frame). top_stocks lists the pinned members active
--    that day, in YEAR-RANK order.
--
--    Pillars / evidence / percentile normalization / score blend / row
--    gates: unchanged from the rolling v2 definition (the four RAW
--    pillars over the pinned set, trailing-250 percentile vs the
--    variant's own history, 0.30/0.30/0.25/0.15 renormalized blend,
--    active pool ≥ 10, turnover pillars required) — plus the same
--    crowdedness_score_ma5 online smoothing layer, computed within the
--    VARIANT's own series. Evidence columns (top1/5/10_share,
--    effective_n) are hot-set-independent and duplicated per variant
--    so each row is self-contained and the API response shape matches
--    the rolling chart exactly.
--
--  ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Year-pinned crowdedness variants — one year's hottest names held fixed across the pool's full history, isolating price, flow, co-movement and valuation from hot-list membership churn.
-- tags: crowdedness, 拥挤度, hot stocks, 热门股, annual, 年度, pinned basket, 固定篮子, concentration, 集中度, comovement, 共振


CREATE TABLE IF NOT EXISTS analysis.market_crowdedness_year (
    sec_type          SMALLINT      NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock 3=industry
    code              TEXT          NOT NULL,
    hot_year          INT           NOT NULL,  -- the pinned calendar year (2025 = the "2025" roster); the dropdown value
    date              DATE          NOT NULL,

    crowdedness_score NUMERIC(10,4) NOT NULL,  -- renormalized weight blend of the four *_pct pillars (percentiles vs the variant's own 250-day history)
    crowdedness_score_ma5 NUMERIC(10,4) NOT NULL,  -- 5-pool-date-ROW moving average of crowdedness_score within the VARIANT's own series (the online smoothing layer; partial mean over the first 4 rows)
    concentration     NUMERIC(10,4) NOT NULL,  -- RAW level pillar: the PINNED hot set's (year-average top 10% by 60-active-day mean amount) share of the day's pool amount
    flow_change       NUMERIC(10,4) NOT NULL,  -- RAW flow pillar: the pinned hot set's share of the pool's gross amount increase
    comovement        NUMERIC(10,4),           -- RAW co-movement pillar: mean corr120(pinned stock return, LOO pool return), -1..1; NULL when no pinned member active that day has a defined correlation
    valuation         NUMERIC(10,4),           -- RAW valuation pillar: mean pinned pe cross-sectional percentile within the pool, 0..1; NULL when no active pinned member has a valid pe
    top1_share        NUMERIC(10,4) NOT NULL,  -- turnover-concentration evidence (hot-set-independent, duplicated from the rolling pass): top 1% by DAY amount, share of the day's pool amount
    top5_share        NUMERIC(10,4) NOT NULL,  -- same for the top 5%
    top10_share       NUMERIC(10,4) NOT NULL,  -- same for the top 10%
    effective_n       NUMERIC(10,4) NOT NULL,  -- turnover-concentration evidence (hot-set-independent, duplicated from the rolling pass): effective traded-name count (Σamt)²/Σamt²
    concentration_pct NUMERIC(10,4) NOT NULL,  -- concentration vs the variant's own trailing 250-day history (percentile, 0..1)
    flow_pct          NUMERIC(10,4) NOT NULL,  -- flow_change vs the variant's own trailing 250-day history (percentile, 0..1)
    comovement_pct    NUMERIC(10,4),           -- comovement vs the variant's own trailing 250-day history (percentile, 0..1); NULL where comovement is NULL
    valuation_pct     NUMERIC(10,4),           -- valuation vs the variant's own trailing 250-day history (percentile, 0..1); NULL where valuation is NULL
    top_stocks        TEXT          NOT NULL,  -- comma-joined names of the pinned members ACTIVE that day, year-rank order (not-yet-listed / already-exited members naturally absent)

    CONSTRAINT pk_market_crowdedness_year PRIMARY KEY (code, sec_type, hot_year, date),
    CONSTRAINT chk_market_crowdedness_year_sec_type
        CHECK (sec_type IN (0, 1, 2, 3))  -- label codes: 0=etf 1=index 2=stock 3=industry
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'market_crowdedness_year', 16);

CREATE INDEX IF NOT EXISTS idx_market_crowdedness_year_date
    ON analysis.market_crowdedness_year (date);
CREATE INDEX IF NOT EXISTS idx_market_crowdedness_year_sty_date
    ON analysis.market_crowdedness_year (sec_type, hot_year, date);

COMMENT ON TABLE  analysis.market_crowdedness_year IS 'Year-pinned hot-set crowdedness variants (2026-11-14): per-(code, sec_type, hot_year, date) crowdedness curve computed with the hot basket FROZEN to one year''s hottest names (year-average top 10% by 60-active-day mean amount, min 5 — frozen in analysis.market_crowdedness_hot_sets on year close) and applied to the pool''s ENTIRE date span; a pinned member contributes only on dates it actually traded (listing/exit masking). Everything else — pillars, percentile normalization, score blend, gates — identical to analysis.market_crowdedness v2. Serves the Market Crowdedness page dropdown via /api/analysis/market-crowdedness/chart?hot_year=… Built by analysis.market_crowdedness; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.market_crowdedness_year.sec_type          IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf, 1 = index (CSI-style index), 2 = stock, 3 = industry (composite industry, code = stats.sec_classification.industry_id). This pipeline is scoped to 1 (index) and 3 (industry), same as the rolling table.';
COMMENT ON COLUMN analysis.market_crowdedness_year.code              IS 'Security code (bare index code e.g. 000001; sec_type=3: industry_id e.g. BANKS).';
COMMENT ON COLUMN analysis.market_crowdedness_year.hot_year          IS 'The pinned calendar year (2025 = the "2025" roster) — the UI dropdown value. The year''s hot set = year-average top max(ceil(0.1·mean active_n over the year''s trading days), 5) members by mean 60-active-day amount over the year''s own trading days (year-to-date while open, frozen in analysis.market_crowdedness_hot_sets once closed).';
COMMENT ON COLUMN analysis.market_crowdedness_year.date              IS 'Trading date — the variant spans the pool''s ENTIRE computed history (a 2025 basket is hot from the pool''s first defined date to today).';
COMMENT ON COLUMN analysis.market_crowdedness_year.crowdedness_score IS 'Market crowdedness (0..1, higher = more crowded) of the PINNED basket: renormalized weight blend 0.30*concentration_pct + 0.30*flow_pct + 0.25*comovement_pct + 0.15*valuation_pct over the pillars defined on the date (turnover pillars required); each *_pct is the pillar''s percentile vs the VARIANT''s own trailing-250-pool-date-row history (>= 60 non-NULL obs). Blended from UNROUNDED percentiles, stored rounded to 4dp. Rows exist only where defined.';
COMMENT ON COLUMN analysis.market_crowdedness_year.crowdedness_score_ma5 IS 'The ONLINE smoothing layer (added 2026-11-14): 5-pool-date-ROW moving average of crowdedness_score within THIS variant''s own series (min 1 row — partial mean over the series head). Same contract as the rolling table''s ma5.';
COMMENT ON COLUMN analysis.market_crowdedness_year.concentration     IS 'RAW level pillar (0..1): the PINNED hot set''s share of the pool''s 5-ACTIVE-DAY MEAN trading amount on the date — pinned members not active that day (suspended, not yet listed, already exited) contribute nothing by construction.';
COMMENT ON COLUMN analysis.market_crowdedness_year.flow_change       IS 'RAW flow pillar (0..1): the pinned hot set''s share of the pool''s GROSS increase of the 5-active-day MEAN amount (Δma5 positive part). No row when the pool''s gross increase is zero.';
COMMENT ON COLUMN analysis.market_crowdedness_year.comovement        IS 'RAW co-movement pillar (−1..1): mean over the pinned members ACTIVE that day of the trailing-120-active-day Pearson correlation between the stock''s daily return and the pool''s leave-one-out equal-weight return (>= 30 joint-valid obs, positive variance). NULL when no active pinned member has a defined correlation.';
COMMENT ON COLUMN analysis.market_crowdedness_year.valuation         IS 'RAW valuation pillar (0..1, 0.5 = pinned names priced at the pool median): mean over the pinned members ACTIVE that day of each stock''s pe cross-sectional percentile within the day''s active pool. NULL when no active pinned member has a valid pe.';
COMMENT ON COLUMN analysis.market_crowdedness_year.top1_share        IS 'Turnover-concentration evidence (0..1, hot-set-INDEPENDENT — same value as the rolling table''s row on the date, duplicated so each variant row is self-contained): share of the day''s pool trading amount captured by the top 1% of the active pool by DAY amount (min 1 name).';
COMMENT ON COLUMN analysis.market_crowdedness_year.top5_share        IS 'Turnover-concentration evidence (0..1, hot-set-independent duplicate): top 5% by DAY amount, share of the day''s pool amount.';
COMMENT ON COLUMN analysis.market_crowdedness_year.top10_share       IS 'Turnover-concentration evidence (0..1, hot-set-independent duplicate): top 10% by DAY amount, share of the day''s pool amount.';
COMMENT ON COLUMN analysis.market_crowdedness_year.effective_n       IS 'Turnover-concentration evidence (1..active_n, hot-set-independent duplicate): effective traded-name count (Σ amt)² / Σ amt² (inverse Herfindahl).';
COMMENT ON COLUMN analysis.market_crowdedness_year.concentration_pct IS 'concentration read against the VARIANT''s own history (0..1): share of non-NULL concentration values in the trailing 250 pool-date rows (within this hot_year variant) that are <= the day''s value (>= 60 non-NULL obs required). NOT NULL — a required score pillar.';
COMMENT ON COLUMN analysis.market_crowdedness_year.flow_pct          IS 'flow_change read against the VARIANT''s own history (0..1): share of non-NULL flow_change values in the trailing 250 pool-date rows (within this hot_year variant) that are <= the day''s value (>= 60 non-NULL obs required). NOT NULL — a required score pillar.';
COMMENT ON COLUMN analysis.market_crowdedness_year.comovement_pct    IS 'comovement read against the VARIANT''s own history (0..1): share of non-NULL comovement values in the trailing 250 pool-date rows (within this hot_year variant) that are <= the day''s value (>= 60 non-NULL obs required). NULL where comovement is NULL (the pillar drops out of the score blend with its weight).';
COMMENT ON COLUMN analysis.market_crowdedness_year.valuation_pct     IS 'valuation read against the VARIANT''s own history (0..1): share of non-NULL valuation values in the trailing 250 pool-date rows (within this hot_year variant) that are <= the day''s value (>= 60 non-NULL obs required). NULL where valuation is NULL (the pillar drops out of the score blend with its weight).';
COMMENT ON COLUMN analysis.market_crowdedness_year.top_stocks        IS 'Evidence for tooltips: comma-joined display names of the PINNED members active that day, in year-rank order — the fixed basket minus suspended / not-yet-listed / already-exited names. Free-form TEXT (open vocabulary), not a label_dim domain.';
