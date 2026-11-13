
-- ----------------------------------------------------------------------------
-- Table: etf_ohlcv
--   ← Raw OHLCV (yuan)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each ETF's daily OHLC prices plus its holdings' harmonic-weighted price-earnings ratio.
-- tags: etf, ohlcv, K线, pe, 市盈率

CREATE TABLE IF NOT EXISTS stats.etf_basic_stats (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    prev_close                NUMERIC(18,4),
    open                      NUMERIC(18,4),
    high                      NUMERIC(18,4),
    low                       NUMERIC(18,4),
    close                     NUMERIC(18,4),
    pct_change                NUMERIC(10,4),
    pe                        NUMERIC(18,4),
    eps                       NUMERIC(18,6),
    is_ohl_estimated          BOOLEAN       NOT NULL DEFAULT FALSE,
    has_intraday_5mins        BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_etf_basic_stats PRIMARY KEY (code, date),
    CONSTRAINT fk_etf_basic_stats_date_code FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_basic_stats', 8);

COMMENT ON TABLE  stats.etf_basic_stats                    IS 'ETF raw basic_stats (yuan) + pe (harmonic-weighted constituent PE).';
COMMENT ON COLUMN stats.etf_basic_stats.is_ohl_estimated  IS 'TRUE when open/high/low were synthesized (not from source CSV) because the source row carries close-only data. Synthesis: open = previous close of the same code (NULL when no predecessor exists), high = low = close. FALSE when any real OHLC component came from the source.';
COMMENT ON COLUMN stats.etf_basic_stats.has_intraday_5mins IS 'TRUE when 5-minute intraday bars exist for this (date, code) (reserved for future ETF intraday support).';
COMMENT ON COLUMN stats.etf_basic_stats.pe                 IS 'Price-to-earnings ratio (PE). Computed by builds.etf via HARMONIC weighting of constituent stock PE from stats.stock_basic_stats by the LATEST stats.sec_composition snapshot (source_type=etf, temporal extrapolation): PE_etf = SUM(w_i) / SUM(w_i / PE_i). Loss-making constituents (NULL PE) excluded from both numerator and denominator. NULL when no composition or no constituent has positive PE.';
COMMENT ON COLUMN stats.etf_basic_stats.eps                IS 'Implied earnings per share (EPS), in yuan per single share, derived from the identity PE = price / EPS as eps = close / pe. NULL when pe is NULL (no composition or all constituents loss-making) or close is NULL. Populated by builds/etf/__main__.py at insert time (recomputed when the harmonic PE is backfilled).';


-- The legacy (code, date) secondary indexes are superseded by the
-- code-first PK; the date-first indexes below keep cross-code scans cheap.

CREATE INDEX IF NOT EXISTS idx_etf_basic_stats_date
    ON stats.etf_basic_stats (date);


-- Date-first walk restricted to REAL closes: the trading-signals services
-- derive the last-20-trading-days window and the date-selector roster with
-- "SELECT DISTINCT date … WHERE close IS NOT NULL ORDER BY date DESC
-- OFFSET/LIMIT" per request — through the plain (date) index that walk
-- visits every close-NULL row's heap tuple too (~2k buffer hits per fetch).
-- The partial index pre-filters close IS NOT NULL, so the walk is an
-- index-only backward scan that stops at the OFFSET/LIMIT boundary.
CREATE INDEX IF NOT EXISTS idx_etf_basic_stats_date_closed
    ON stats.etf_basic_stats (date DESC)
    WHERE close IS NOT NULL;
