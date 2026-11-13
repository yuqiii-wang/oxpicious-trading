-- ============================================================================
--  Market Participation — one row per trading day for the WHOLE A-share
--  market's participation gauges: total exchange turnover, the SSE share
--  of that turnover, and the advance/decline breadth of the stock
--  universe.
--
--    Table: analysis.market_participation
--    PK: (date) — unpartitioned (~250 rows/year; industry_hypes_seasonal
--    precedent for small unpartitioned analysis tables)
--
--  POPULATION
--    analysis.market_participation (Python module). Per project rule,
--    ALL INSERTs are in Python — no raw INSERT...SELECT SQL in this
--    file. Incremental by missing dates; --force truncates and
--    regenerates.
--
--    SOURCES (all read via database/sql_views/analysis/
--    market_participation/ templates):
--      turnover  — stats.exchange_trading_amt pivoted per exchange.
--                  PROXY CAVEAT: each exchange is proxied by ONE
--                  representative broad index (SS -> 000001 上证指数,
--                  SZ -> 399001 深证成指), NOT a true exchange-wide sum.
--                  The "total" is the SS+SZ proxy pair; BJ has no
--                  representative and is not covered.
--      breadth   — stats.stock_basic_stats pct_change sign counts over
--                  the whole stock universe (NULL pct_change excluded:
--                  suspended / placeholder rows drop out of every
--                  count).
--
--  METRIC DEFINITIONS
--    total_trading_amount     = ss_trading_amount + sz_trading_amount.
--    ss_share                 = ss / (ss + sz) — RAW daily share, the
--                               piece analysis.mov_ave_trading_amt only
--                               stores smoothed.
--    total_amt_ma{W}          = W-day rolling mean of
--                               total_trading_amount (NULL until W rows).
--    total_amt_vs_ma{W}       = (total - maW) / maW, signed fraction.
--    total_amt_pct_rank_252   = share of non-NULL total values in the
--                               trailing 252 rows <= today's (>= 60 obs
--                               required; NULL before that — the
--                               analysis.market_crowdedness *_pct
--                               convention).
--    ss_share_ma{W}           = W-day rolling mean of ss_share.
--    ss_share_vs_ma20_pp      = (ss_share - ss_share_ma20) * 100,
--                               percentage points.
--    ss_share_slope20_pp      = OLS slope of ss_share over the trailing
--                               20 rows, pp/day (closed form via rolling
--                               sums; NULL until 20 rows).
--    adv/dec/flat_count       = COUNT(pct_change > 0 / < 0 / = 0) over
--                               the day's stocks.
--    ad_ratio                 = adv / NULLIF(dec, 0) (NULL on zero
--                               decliners); 1 = balanced.
--    median/mean_pct_change   = percentile_cont(0.5) / AVG of the day's
--                               non-NULL pct_change.
--    ad_ratio_ma{W}           = W-day rolling mean of ad_ratio.
--    ad_ratio_pct_rank_252    = ss_share-style trailing percentile of
--                               ad_ratio.
--
--    Rows exist only where total_trading_amount is defined (both proxy
--    rows present); breadth columns are NULL on dates with no stock
--    rows.
--  ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The whole market's daily participation gauges on one row per trading day — total SS+SZ proxy turnover with its percentile, the raw SSE share of that turnover, and the advance/decline breadth of the stock universe.
-- tags: whole market, 全市场, two exchanges, 两市, 沪深, turnover, 成交额, participation, 参与度, activity, 活跃度, sse share, 上证占比, market share, 成交占比, breadth, 市场广度, advance decline, 涨跌家数, 放量, 缩量


CREATE TABLE IF NOT EXISTS analysis.market_participation (
    date                      DATE          NOT NULL,

    -- Market turnover (stats.exchange_trading_amt proxies)
    total_trading_amount      NUMERIC(24,4) NOT NULL,  -- SS + SZ proxy total (yuan)
    ss_trading_amount         NUMERIC(24,4) NOT NULL,  -- SSE proxy (000001) turnover (yuan)
    sz_trading_amount         NUMERIC(24,4) NOT NULL,  -- SZSE proxy (399001) turnover (yuan)
    total_amt_ma5             NUMERIC(24,4),           -- 5-day rolling mean of total_trading_amount
    total_amt_ma20            NUMERIC(24,4),           -- 20-day rolling mean
    total_amt_ma60            NUMERIC(24,4),           -- 60-day rolling mean
    total_amt_vs_ma5          NUMERIC(10,4),           -- (total - ma5) / ma5, signed fraction
    total_amt_vs_ma20         NUMERIC(10,4),           -- (total - ma20) / ma20
    total_amt_vs_ma60         NUMERIC(10,4),           -- (total - ma60) / ma60
    total_amt_pct_rank_252    NUMERIC(10,4),           -- trailing-252-row percentile of total (0..1), >= 60 obs

    -- SSE share of market turnover (raw daily share + gauges)
    ss_share                  NUMERIC(10,6) NOT NULL,  -- ss / (ss + sz), raw daily share 0..1
    ss_share_ma5              NUMERIC(10,6),           -- 5-day rolling mean of ss_share
    ss_share_ma20             NUMERIC(10,6),           -- 20-day rolling mean
    ss_share_ma60             NUMERIC(10,6),           -- 60-day rolling mean
    ss_share_vs_ma20_pp       NUMERIC(10,4),           -- (ss_share - ss_share_ma20) * 100, percentage points
    ss_share_slope20_pp       NUMERIC(10,6),           -- trailing-20-row OLS slope of ss_share, pp/day

    -- Advance/decline breadth (stats.stock_basic_stats whole universe)
    adv_count                 INTEGER,                 -- stocks with pct_change > 0
    dec_count                 INTEGER,                 -- stocks with pct_change < 0
    flat_count                INTEGER,                 -- stocks with pct_change = 0
    ad_ratio                  NUMERIC(10,4),           -- adv / NULLIF(dec, 0); NULL on zero decliners
    median_pct_change         NUMERIC(10,4),           -- percentile_cont(0.5) of the day's pct_change
    mean_pct_change           NUMERIC(10,4),           -- AVG of the day's pct_change
    ad_ratio_ma5              NUMERIC(10,4),           -- 5-day rolling mean of ad_ratio
    ad_ratio_ma20             NUMERIC(10,4),           -- 20-day rolling mean
    ad_ratio_pct_rank_252     NUMERIC(10,4),           -- trailing-252-row percentile of ad_ratio (0..1), >= 60 obs

    CONSTRAINT pk_market_participation PRIMARY KEY (date)
);

CREATE INDEX IF NOT EXISTS idx_market_participation_date
    ON analysis.market_participation (date);

COMMENT ON TABLE  analysis.market_participation IS 'One row per trading day for the whole A-share market''s participation gauges: total exchange turnover (SS+SZ proxy from stats.exchange_trading_amt — each exchange proxied by one representative index, NOT a true exchange-wide sum), the SSE share of that turnover (raw daily share + MA gauges + slope), and the advance/decline breadth of the stock universe (stats.stock_basic_stats sign counts, A/D ratio, median/mean change). Built by analysis.market_participation; all INSERTs in Python per project rule.';

COMMENT ON COLUMN analysis.market_participation.date                   IS 'Trading date (one row per trading day).';
COMMENT ON COLUMN analysis.market_participation.total_trading_amount  IS 'SS + SZ proxy total trading turnover (yuan) on this date, summed from stats.exchange_trading_amt. PROXY: SS is proxied by 000001 上证指数 and SZ by 399001 深证成指 (each exchange''s representative broad index), NOT a true exchange-wide sum; BJ is not covered.';
COMMENT ON COLUMN analysis.market_participation.ss_trading_amount     IS 'SSE proxy turnover (yuan): stats.exchange_trading_amt exchange=''SS'' (representative index 000001 上证指数).';
COMMENT ON COLUMN analysis.market_participation.sz_trading_amount     IS 'SZSE proxy turnover (yuan): stats.exchange_trading_amt exchange=''SZ'' (representative index 399001 深证成指).';
COMMENT ON COLUMN analysis.market_participation.total_amt_ma5         IS '5-trading-day rolling mean of total_trading_amount (yuan). NULL until 5 rows.';
COMMENT ON COLUMN analysis.market_participation.total_amt_ma20        IS '20-trading-day rolling mean of total_trading_amount. NULL until 20 rows.';
COMMENT ON COLUMN analysis.market_participation.total_amt_ma60        IS '60-trading-day rolling mean of total_trading_amount. NULL until 60 rows.';
COMMENT ON COLUMN analysis.market_participation.total_amt_vs_ma5      IS 'Signed fractional gap (total_trading_amount - total_amt_ma5) / total_amt_ma5. Positive = above the 5-day mean (放量-side), negative = below (缩量-side).';
COMMENT ON COLUMN analysis.market_participation.total_amt_vs_ma20     IS 'Signed fractional gap vs total_amt_ma20.';
COMMENT ON COLUMN analysis.market_participation.total_amt_vs_ma60     IS 'Signed fractional gap vs total_amt_ma60.';
COMMENT ON COLUMN analysis.market_participation.total_amt_pct_rank_252 IS 'Share of non-NULL total_trading_amount values in the trailing 252 rows that are <= today''s (0..1, >= 60 non-NULL obs required). Where today''s turnover sits in its own trailing-year distribution.';
COMMENT ON COLUMN analysis.market_participation.ss_share              IS 'RAW daily SSE share of market turnover: ss_trading_amount / total_trading_amount, 0..1. The raw daily share analysis.mov_ave_trading_amt stores only smoothed (market_share_ma{W}).';
COMMENT ON COLUMN analysis.market_participation.ss_share_ma5          IS '5-trading-day rolling mean of ss_share. NULL until 5 rows.';
COMMENT ON COLUMN analysis.market_participation.ss_share_ma20         IS '20-trading-day rolling mean of ss_share.';
COMMENT ON COLUMN analysis.market_participation.ss_share_ma60         IS '60-trading-day rolling mean of ss_share.';
COMMENT ON COLUMN analysis.market_participation.ss_share_vs_ma20_pp   IS '(ss_share - ss_share_ma20) * 100, in percentage points. Positive = SSE share above its 20-day mean (沪强于深 side), negative = below.';
COMMENT ON COLUMN analysis.market_participation.ss_share_slope20_pp   IS 'OLS slope of ss_share regressed on row position over the trailing 20 rows, in percentage points per day (closed-form via rolling sums). Positive = SSE share trending up.';
COMMENT ON COLUMN analysis.market_participation.adv_count             IS 'Number of stocks with pct_change > 0 on this date (stats.stock_basic_stats whole universe; NULL pct_change rows — suspended / placeholder — excluded from every count). NULL when the day has no stock rows.';
COMMENT ON COLUMN analysis.market_participation.dec_count             IS 'Number of stocks with pct_change < 0.';
COMMENT ON COLUMN analysis.market_participation.flat_count            IS 'Number of stocks with pct_change = 0.';
COMMENT ON COLUMN analysis.market_participation.ad_ratio             IS 'adv_count / NULLIF(dec_count, 0) — the advance-decline ratio; 1 = balanced, NULL on zero decliners.';
COMMENT ON COLUMN analysis.market_participation.median_pct_change     IS 'Median (percentile_cont(0.5)) of the day''s non-NULL pct_change across the stock universe.';
COMMENT ON COLUMN analysis.market_participation.mean_pct_change      IS 'Mean of the day''s non-NULL pct_change across the stock universe.';
COMMENT ON COLUMN analysis.market_participation.ad_ratio_ma5         IS '5-trading-day rolling mean of ad_ratio. NULL until 5 non-NULL rows.';
COMMENT ON COLUMN analysis.market_participation.ad_ratio_ma20        IS '20-trading-day rolling mean of ad_ratio.';
COMMENT ON COLUMN analysis.market_participation.ad_ratio_pct_rank_252 IS 'Share of non-NULL ad_ratio values in the trailing 252 rows that are <= today''s (0..1, >= 60 obs required).';
