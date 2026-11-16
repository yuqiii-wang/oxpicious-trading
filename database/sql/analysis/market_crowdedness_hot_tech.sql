-- ============================================================================
--  Market Crowdedness, hot-stock tech aggregates — per-(sec_type, code,
--  date) typical technical data over the day's rolling hot set.
--
--    Table: analysis.market_crowdedness_hot_tech
--    PK: (code, sec_type, date)
--
--  WHY (2026-11-14): the crowdedness tables score HOW crowded the hot
--  names are; they do not persist WHAT the hot names look like
--  technically. This table stores the hot set's typical technical
--  profile — where its members sit vs their moving averages, their
--  momentum (RSI / MACD histogram / ROC), whether they trade on
--  expanding volume, and their typical daily return — so the
--  crowdedness curve can be read together with the technical state of
--  the very names that make it hot.
--
--  Serves future reads alongside the Commons "Market Crowdedness" page
--  (/analysis/commons/market-crowdedness); the hot set is IDENTICAL to
--  the rolling analysis.market_crowdedness hot set by construction
--  (same mask, same chunk pass — the names also ride that table's
--  top_stocks evidence on the same key, so this table carries no name
--  column).
--
--  POPULATION
--    analysis.market_crowdedness (Python module — the aggregation runs
--    in the same per-pool chunk pass as the rolling score, on a slim
--    column-subset frame; the rolling/pinned outputs stay
--    bit-identical). Per project rule, ALL INSERTs are in Python — no
--    raw INSERT...SELECT SQL in this file.
--
--    sec_type ∈ ('index' | 'industry') — same scope as the rolling
--    table (the shared label_dim 'sec_type' domain is 0=etf 1=index
--    2=stock 3=industry; the CHECK keeps the full domain so the column
--    decodes like every other analysis sec_type column).
--
--  METRIC DEFINITION (2026-11-14):
--    Hot set H(t) = the rolling crowdedness hot set: top 10% of the
--    day's active pool by the 60-active-day MEAN trading amount
--    (floored at 5 names, >= 20 active days to rank). Active pool
--    A(t) = members with trading_amount > 0 on t (stats.
--    stock_liquidity_margin).
--
--    EIGHT PER-STOCK METRICS (decimal fractions unless stated; the
--    return series everywhere = pct_change/100, falling back to
--    close/prev_close − 1 where the source leaves pct_change NULL —
--    the SSE archive, same series the comovement pillar uses):
--      ma5_ratio    — precomputed close/ma5 − 1 (stats.stock_tech_stats;
--                     0 = at MA5)                                     frac
--      px_vs_ma20   — close/ma20 − 1 (precomputed ma20)               frac
--      px_vs_ma60   — close/ma60 − 1 (precomputed ma60)               frac
--      macd_hist    — precomputed MACD histogram (price units)
--      rsi14        — RSI(14), SMA-smoothed (Cutler's form: 100·mean
--                     gain / (mean gain + mean loss) over the stock's
--                     trailing-14-ACTIVE-ROW window, >= 14 valid obs;
--                     the GPU-native rolling-mean formulation — NOT
--                     Wilder's ewm smoothing; flat windows stay NULL)
--                                                                    0..100
--      roc20        — close / close(−20 active rows) − 1              frac
--      amt_vs_ma20  — trading_amount / MA20(trading_amount over the
--                     stock's own active days, full 20-obs window) − 1
--                     — the per-stock 放量/缩量 gauge (positive =
--                     participation above its own MA20, house volume
--                     semantics: always relative to the stock's own MA,
--                     never absolute yuan)                             frac
--      ret          — the day's return                                 frac
--
--    AGGREGATION: mean_M = equal-weight arithmetic mean over the day's
--    hot members where M is defined; median_M = the median over the
--    same members (robust to one mega-mover). NULL when no hot member
--    has the metric defined.
--
--    PERCENTILES: mean_M_pct / median_M_pct read the aggregate against
--    the POOL'S OWN history — the share of non-NULL aggregate values
--    in the trailing 250 pool-date rows (<= t) that are <= the day's
--    4dp-rounded value, requiring >= 60 non-NULL observations (the
--    crowdedness v2 *_pct machinery, shared PCT_WINDOW/MIN_PCT_OBS).
--    0..1, higher = the hot set's typical reading is more extreme vs
--    the pool's own history. NULL during the pool's percentile warm-up
--    and where the aggregate itself is NULL.
--
--  ROW GATE: rows exist only where the active pool >= 10 (below it the
--  floored hot set would cover half the pool or more — the crowdedness
--  gate rationale) AND the hot set is non-empty. UNLIKE the score
--  table, the percentiles are NOT required for a row — this is a raw
--  evidence table, so rows exist on warm-up days the score table
--  drops, with the *_pct columns NULL.
--
--  ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Per index or industry pool and trading date, the typical technical profile of its hot stocks — where the hottest names sit versus their moving averages, their momentum (RSI, MACD histogram, 20-day ROC), volume expansion and daily return, aggregated as mean and median over the day's hot set with each aggregate also read as a percentile against the pool's own history.
-- tags: hot stocks, 热门股, technical indicators, 技术指标, moving average, 均线, RSI, MACD, momentum, 动量, volume expansion, 放量, crowdedness, 拥挤度


CREATE TABLE IF NOT EXISTS analysis.market_crowdedness_hot_tech (
    sec_type SMALLINT NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock 3=industry
    code     TEXT     NOT NULL,
    date     DATE     NOT NULL,
    hot_n    INT      NOT NULL,  -- the day's hot-set size (members aggregated)

    mean_ma5_ratio      NUMERIC(10,4),  -- mean over the hot set of precomputed close/ma5 - 1 (0 = at MA5)
    mean_ma5_ratio_pct  NUMERIC(10,4),  -- mean_ma5_ratio vs the pool's own trailing 250-day history (percentile, 0..1)
    median_ma5_ratio    NUMERIC(10,4),  -- median over the hot set of ma5_ratio
    median_ma5_ratio_pct NUMERIC(10,4),  -- median_ma5_ratio vs the pool's own trailing 250-day history (percentile, 0..1)

    mean_px_vs_ma20      NUMERIC(10,4),  -- mean over the hot set of close/ma20 - 1
    mean_px_vs_ma20_pct  NUMERIC(10,4),  -- mean_px_vs_ma20 vs the pool's own trailing 250-day history (percentile, 0..1)
    median_px_vs_ma20    NUMERIC(10,4),  -- median over the hot set of px_vs_ma20
    median_px_vs_ma20_pct NUMERIC(10,4),  -- median_px_vs_ma20 vs the pool's own trailing 250-day history (percentile, 0..1)

    mean_px_vs_ma60      NUMERIC(10,4),  -- mean over the hot set of close/ma60 - 1
    mean_px_vs_ma60_pct  NUMERIC(10,4),  -- mean_px_vs_ma60 vs the pool's own trailing 250-day history (percentile, 0..1)
    median_px_vs_ma60    NUMERIC(10,4),  -- median over the hot set of px_vs_ma60
    median_px_vs_ma60_pct NUMERIC(10,4),  -- median_px_vs_ma60 vs the pool's own trailing 250-day history (percentile, 0..1)

    mean_macd_hist      NUMERIC(10,4),  -- mean over the hot set of the precomputed MACD histogram (price units)
    mean_macd_hist_pct  NUMERIC(10,4),  -- mean_macd_hist vs the pool's own trailing 250-day history (percentile, 0..1)
    median_macd_hist    NUMERIC(10,4),  -- median over the hot set of macd_hist
    median_macd_hist_pct NUMERIC(10,4),  -- median_macd_hist vs the pool's own trailing 250-day history (percentile, 0..1)

    mean_rsi14      NUMERIC(10,4),  -- mean over the hot set of RSI(14), SMA-smoothed (Cutler form, 0..100)
    mean_rsi14_pct  NUMERIC(10,4),  -- mean_rsi14 vs the pool's own trailing 250-day history (percentile, 0..1)
    median_rsi14    NUMERIC(10,4),  -- median over the hot set of rsi14
    median_rsi14_pct NUMERIC(10,4),  -- median_rsi14 vs the pool's own trailing 250-day history (percentile, 0..1)

    mean_roc20      NUMERIC(10,4),  -- mean over the hot set of close/close(-20 active rows) - 1
    mean_roc20_pct  NUMERIC(10,4),  -- mean_roc20 vs the pool's own trailing 250-day history (percentile, 0..1)
    median_roc20    NUMERIC(10,4),  -- median over the hot set of roc20
    median_roc20_pct NUMERIC(10,4),  -- median_roc20 vs the pool's own trailing 250-day history (percentile, 0..1)

    mean_amt_vs_ma20      NUMERIC(10,4),  -- mean over the hot set of amount/MA20(amount) - 1 (the per-stock 放量 gauge)
    mean_amt_vs_ma20_pct  NUMERIC(10,4),  -- mean_amt_vs_ma20 vs the pool's own trailing 250-day history (percentile, 0..1)
    median_amt_vs_ma20    NUMERIC(10,4),  -- median over the hot set of amt_vs_ma20
    median_amt_vs_ma20_pct NUMERIC(10,4),  -- median_amt_vs_ma20 vs the pool's own trailing 250-day history (percentile, 0..1)

    mean_ret      NUMERIC(10,4),  -- mean over the hot set of the day's return (decimal)
    mean_ret_pct  NUMERIC(10,4),  -- mean_ret vs the pool's own trailing 250-day history (percentile, 0..1)
    median_ret    NUMERIC(10,4),  -- median over the hot set of the day's return
    median_ret_pct NUMERIC(10,4),  -- median_ret vs the pool's own trailing 250-day history (percentile, 0..1)

    CONSTRAINT pk_market_crowdedness_hot_tech PRIMARY KEY (code, sec_type, date),
    CONSTRAINT chk_market_crowdedness_hot_tech_sec_type
        CHECK (sec_type IN (0, 1, 2, 3)),  -- label codes: 0=etf 1=index 2=stock 3=industry
    CONSTRAINT chk_market_crowdedness_hot_tech_hot_n
        CHECK (hot_n >= 1)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'market_crowdedness_hot_tech', 16);

CREATE INDEX IF NOT EXISTS idx_market_crowdedness_hot_tech_date
    ON analysis.market_crowdedness_hot_tech (date);
CREATE INDEX IF NOT EXISTS idx_market_crowdedness_hot_tech_sec_type_date
    ON analysis.market_crowdedness_hot_tech (sec_type, date);

COMMENT ON TABLE  analysis.market_crowdedness_hot_tech IS 'Per-(code, sec_type, date) typical technical data over the day''s ROLLING hot set (2026-11-14): the top-10%-by-60-active-day-mean-amount hot names of each index/industry pool, aggregated as equal-weight mean + median across eight per-stock metrics (trend stretch ma5_ratio/px_vs_ma20/px_vs_ma60, momentum macd_hist/rsi14/roc20, the per-stock volume-expansion gauge amt_vs_ma20, and the day''s return), each aggregate also read as a percentile vs the pool''s own trailing-250-pool-date history (>= 60 obs). Same hot set as analysis.market_crowdedness by construction (same chunk pass; the names ride that table''s top_stocks). Raw evidence — percentiles NOT required for a row (NULL during warm-up). Built by analysis.market_crowdedness; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.sec_type IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf, 1 = index (CSI-style index), 2 = stock, 3 = industry (composite industry, code = stats.sec_classification.industry_id). This pipeline is scoped to 1 (index) and 3 (industry), same as the crowdedness tables.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.code IS 'Pool code (bare index code e.g. 000001; sec_type=3: industry_id e.g. BANKS).';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.date IS 'Trading date.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.hot_n IS 'The day''s hot-set size = the number of hot members the aggregates cover (top max(ceil(0.1*active_n), 5) of the active pool by 60-active-day mean amount, >= 20 active days to rank). Per-metric member counts can be lower where the metric is undefined (NULL pe-style exclusions).';

COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_ma5_ratio IS 'Equal-weight MEAN over the day''s hot members of the precomputed ma5_ratio (stats.stock_tech_stats: close/ma5 - 1; 0 = at MA5, positive = stretched above). NULL when no hot member has it defined.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_ma5_ratio_pct IS 'mean_ma5_ratio read against the pool''s own history (0..1): share of non-NULL mean_ma5_ratio values in the trailing 250 pool-date rows that are <= the day''s 4dp-rounded value (>= 60 non-NULL obs required). NULL during warm-up / where the aggregate is NULL. Higher = the hot set is typically more stretched above its MA5 than usual for this pool.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_ma5_ratio IS 'MEDIAN over the day''s hot members of ma5_ratio — the robust central value (one mega-mover cannot drag it). NULL when no hot member has it defined.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_ma5_ratio_pct IS 'median_ma5_ratio read against the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs), same mechanics as mean_ma5_ratio_pct.';

COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_px_vs_ma20 IS 'Equal-weight MEAN over the hot set of close/ma20 - 1 (ma20 precomputed in stats.stock_tech_stats; decimal fraction, 0.05 = 5% above MA20) — the swing-trend position of the typical hot name.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_px_vs_ma20_pct IS 'mean_px_vs_ma20 vs the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs).';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_px_vs_ma20 IS 'MEDIAN over the hot set of px_vs_ma20.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_px_vs_ma20_pct IS 'median_px_vs_ma20 vs the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs).';

COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_px_vs_ma60 IS 'Equal-weight MEAN over the hot set of close/ma60 - 1 — the intermediate-trend position of the typical hot name (decimal fraction).';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_px_vs_ma60_pct IS 'mean_px_vs_ma60 vs the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs).';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_px_vs_ma60 IS 'MEDIAN over the hot set of px_vs_ma60.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_px_vs_ma60_pct IS 'median_px_vs_ma60 vs the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs).';

COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_macd_hist IS 'Equal-weight MEAN over the hot set of the precomputed MACD histogram (stats.stock_tech_stats.macd_hist; price units — EMA12 − EMA26 minus its signal line; sign = momentum direction, magnitude = its strength at the stock''s own price scale).';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_macd_hist_pct IS 'mean_macd_hist vs the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs).';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_macd_hist IS 'MEDIAN over the hot set of macd_hist.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_macd_hist_pct IS 'median_macd_hist vs the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs).';

COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_rsi14 IS 'Equal-weight MEAN over the hot set of RSI(14), SMA-smoothed (Cutler''s form: 100 * mean-gain / (mean-gain + mean-loss) over the stock''s trailing-14-ACTIVE-ROW return series, >= 14 valid obs; flat windows NULL). The GPU-native rolling-mean formulation — deliberately NOT Wilder''s ewm smoothing. 0..100, > 70 overbought / < 30 oversold zones.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_rsi14_pct IS 'mean_rsi14 vs the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs).';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_rsi14 IS 'MEDIAN over the hot set of rsi14.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_rsi14_pct IS 'median_rsi14 vs the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs).';

COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_roc20 IS 'Equal-weight MEAN over the hot set of the 20-ACTIVE-ROW rate of change (close / close 20 stock rows ago - 1; decimal fraction) — the month-scale momentum of the typical hot name.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_roc20_pct IS 'mean_roc20 vs the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs).';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_roc20 IS 'MEDIAN over the hot set of roc20.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_roc20_pct IS 'median_roc20 vs the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs).';

COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_amt_vs_ma20 IS 'Equal-weight MEAN over the hot set of trading_amount / MA20(trading_amount over the stock''s own active days, full 20-obs window) - 1 — the per-stock 放量/缩量 gauge (positive = participation above its own MA20). House volume semantics: relative to the stock''s OWN moving average, never absolute yuan.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_amt_vs_ma20_pct IS 'mean_amt_vs_ma20 vs the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs).';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_amt_vs_ma20 IS 'MEDIAN over the hot set of amt_vs_ma20.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_amt_vs_ma20_pct IS 'median_amt_vs_ma20 vs the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs).';

COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_ret IS 'Equal-weight MEAN over the hot set of the day''s return (decimal; pct_change/100, falling back to close/prev_close - 1 where the source leaves pct_change NULL — the SSE archive). The typical daily move of the hot names.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.mean_ret_pct IS 'mean_ret vs the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs).';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_ret IS 'MEDIAN over the hot set of the day''s return.';
COMMENT ON COLUMN analysis.market_crowdedness_hot_tech.median_ret_pct IS 'median_ret vs the pool''s own trailing-250-row history (0..1, >= 60 non-NULL obs).';
