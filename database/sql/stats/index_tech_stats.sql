
-- ----------------------------------------------------------------------------
-- Table: index_tech_stats
--   ← Technical indicators (moving averages)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each index's daily moving-average and EMA values.
-- tags: index, 指数, indicators, 技术指标, moving average, 均线, ema

CREATE TABLE IF NOT EXISTS stats.index_tech_stats (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    ma5                       NUMERIC(18,4),
    ma5_ratio                 NUMERIC(10,6),
    ma20                      NUMERIC(18,4),
    ma60                      NUMERIC(18,4),
    ma120                     NUMERIC(18,4),
    ma252                     NUMERIC(18,4),
    ema6                      NUMERIC(18,4),
    ema12                     NUMERIC(18,4),
    ema20                     NUMERIC(18,4),
    ema26                     NUMERIC(18,4),
    ema60                     NUMERIC(18,4),
    ema120                    NUMERIC(18,4),
    ema252                    NUMERIC(18,4),
    ema9                      NUMERIC(18,4),
    macd_line                 NUMERIC(18,4),
    macd_signal               NUMERIC(18,4),
    macd_hist                 NUMERIC(18,4),
    trading_amt_per_pct_change NUMERIC(18,6),

    CONSTRAINT pk_index_tech_stats PRIMARY KEY (code, date),
    CONSTRAINT fk_index_tech_stats_date_code FOREIGN KEY (code, date) REFERENCES stats.index_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'index_tech_stats', 8);

COMMENT ON TABLE  stats.index_tech_stats                    IS 'Index technical indicators (moving averages + EMAs).';
COMMENT ON COLUMN stats.index_tech_stats.ma5               IS '5-day moving average of close.';
COMMENT ON COLUMN stats.index_tech_stats.ma5_ratio         IS 'Close / MA5 - 1 (ratio of price to 5-day MA).';
COMMENT ON COLUMN stats.index_tech_stats.ma20              IS '20-day moving average of close.';
COMMENT ON COLUMN stats.index_tech_stats.ma60              IS '60-day moving average of close.';
COMMENT ON COLUMN stats.index_tech_stats.ma120             IS '120-day moving average of close.';
COMMENT ON COLUMN stats.index_tech_stats.ma252             IS '252-day moving average of close.';
COMMENT ON COLUMN stats.index_tech_stats.ema6              IS '6-day exponential moving average of close (span=6, adjust=False).';
COMMENT ON COLUMN stats.index_tech_stats.ema12             IS '12-day exponential moving average of close (span=12, adjust=False) — the classic MACD fast EMA (replaced the nearby non-standard span 10, 2026-10-26).';
COMMENT ON COLUMN stats.index_tech_stats.ema20             IS '20-day exponential moving average of close (span=20, adjust=False).';
COMMENT ON COLUMN stats.index_tech_stats.ema26             IS '26-day exponential moving average of close (span=26, adjust=False) — the classic MACD slow EMA.';
COMMENT ON COLUMN stats.index_tech_stats.ema60             IS '60-day exponential moving average of close (span=60, adjust=False).';
COMMENT ON COLUMN stats.index_tech_stats.ema120            IS '120-day exponential moving average of close (span=120, adjust=False).';
COMMENT ON COLUMN stats.index_tech_stats.ema252            IS '252-day exponential moving average of close (span=252, adjust=False).';
COMMENT ON COLUMN stats.index_tech_stats.ema9              IS '9-day exponential moving average of close (span=9, adjust=False).';
COMMENT ON COLUMN stats.index_tech_stats.macd_line         IS 'MACD line (DIF): ema12 - ema26 — the fast minus the slow EMA of close (spans 12/26, adjust=False). Materializes the value deep-agent studies used to recompute from raw EMAs.';
COMMENT ON COLUMN stats.index_tech_stats.macd_signal       IS 'MACD signal line (DEA): the 9-span EMA (adjust=False) of macd_line — a path-dependent recursion over the macd_line SERIES, not derivable from any single row.';
COMMENT ON COLUMN stats.index_tech_stats.macd_hist         IS 'MACD histogram: macd_line - macd_signal — the repo macd engine''s plain difference (analysis_signals macd family convention, NOT the 2x Chinese-market variant).';
COMMENT ON COLUMN stats.index_tech_stats.trading_amt_per_pct_change IS 'Intraday net-move liquidity ratio: trading_amount / (close - open), SIGNED — positive on up days, negative on down days (the sign carries the move direction). All inputs from stats.index_basic_stats (trading_amount in yuan; open/close in index points). Zero move (close = open — flat day): denominator floored to 1.0, so the stored value equals the raw trading amount (mov_ave_spread zero-denominator convention — a pragmatic floor, NOT a true ratio). NULL when any input is NULL or |value| >= 1e12 (NUMERIC(18,6) bound). Reciprocal-Amihud liquidity gauge: HIGH = deep book (much capital absorbed per unit of move), LOW = thin market (little capital moved the price a lot). Computed by builds.index.daily.';

CREATE INDEX IF NOT EXISTS idx_index_tech_stats_date
    ON stats.index_tech_stats (date);
