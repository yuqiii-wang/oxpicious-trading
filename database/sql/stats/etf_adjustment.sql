
-- ----------------------------------------------------------------------------
-- Table: etf_adjustment
--   ← Split / dividend adjustment
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each ETF's split and dividend adjustments that splice distributions back into its price series.
-- tags: etf, adjusted price, 复权, dividend, 分红

CREATE TABLE IF NOT EXISTS stats.etf_adjustment (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    cum_split_factor          NUMERIC(18,8) NOT NULL DEFAULT 1.0,
    is_split_event_day        SMALLINT     NOT NULL DEFAULT 0
        CHECK (is_split_event_day IN (0,1)),
    action_type               TEXT,
    implied_dividend_per_share NUMERIC(18,6) NOT NULL DEFAULT 0,
    cum_dividend_per_share    NUMERIC(18,6) NOT NULL DEFAULT 0,
    adj_prev_close            NUMERIC(18,6),
    adj_open                  NUMERIC(18,6),
    adj_high                  NUMERIC(18,6),
    adj_low                   NUMERIC(18,6),
    adj_close                 NUMERIC(18,6),

    CONSTRAINT pk_etf_adjustment PRIMARY KEY (code, date),
    CONSTRAINT fk_etf_adjustment_date_code FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_adjustment', 8);

COMMENT ON TABLE  stats.etf_adjustment               IS 'ETF split / dividend adjustment data.';
COMMENT ON COLUMN stats.etf_adjustment.cum_split_factor IS 'Cumulative split factor (1.0 = no split). Multiply raw OHLC by 1/cum_split_factor to back-adjust.';
COMMENT ON COLUMN stats.etf_adjustment.adj_close     IS 'Split-adjusted close; the frontend uses adj_* when present, otherwise falls back to raw.';

CREATE INDEX IF NOT EXISTS idx_etf_adjustment_date
    ON stats.etf_adjustment (date);


CREATE INDEX IF NOT EXISTS idx_etf_margin_split_events
    ON stats.etf_adjustment (date)
    WHERE is_split_event_day = 1;
