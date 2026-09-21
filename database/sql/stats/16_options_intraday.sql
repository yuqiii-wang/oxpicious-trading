-- ============================================================================
--  Options intraday 5-minute bars (SSE ETF options live stream)
--
--  Source: downloads.stream.sse.price options asset — polls the tstyle
--  endpoints behind https://www.sse.com.cn/assortment/options/price/ every
--  60 s per (underlying, expiry-month) and aggregates 5 samples into one bar.
--  volume/amount are DAY-CUMULATIVE at the source, so per-bar values are
--  derived by subtracting consecutive samples (same rule as the equity
--  stream's trading_shares). Raw snapshots are archived to
--  temps/sse_options_intraday/ and re-loadable via the CSV backfill path.
--
--  FK parent is stats.options_identity — the stream itself writes the
--  identity rows (numeric 合约编码 + 合约简称 from the 当日合约 listing,
--  exchange='SSE'). Pruned by 98_intraday_retention.sql like the other
--  intraday tables.
-- ============================================================================

CREATE TABLE IF NOT EXISTS stats.options_intraday_5min (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    exchange                  TEXT          NOT NULL
        CONSTRAINT chk_options_intraday_5min_exchange
        CHECK (exchange IN ('SSE','SZSE','CFFEX')),
    time                      TIME          NOT NULL,
    open                      NUMERIC(18,4),
    high                      NUMERIC(18,4),
    low                       NUMERIC(18,4),
    close                     NUMERIC(18,4),
    volume                    NUMERIC(24,4),
    amount                    NUMERIC(24,4),
    change                    NUMERIC(18,4),
    change_pct                NUMERIC(10,4),

    CONSTRAINT pk_options_intraday_5min PRIMARY KEY (contract_code, date, time),
    CONSTRAINT fk_options_intraday_5min_date_code FOREIGN KEY (contract_code, date)
        REFERENCES stats.options_identity(contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by contract_code — created via the shared
-- util (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_intraday_5min', 8);

COMMENT ON TABLE  stats.options_intraday_5min IS 'SSE ETF-option 5-minute intraday bars streamed from the tstyle endpoints behind https://www.sse.com.cn/assortment/options/price/.';
COMMENT ON COLUMN stats.options_intraday_5min.time     IS 'Bar end time (HH:MM:SS) on the 5-min grid (09:35 … 15:00); post-close EOD captures clamp to 15:00.';
COMMENT ON COLUMN stats.options_intraday_5min.volume   IS 'Per-bar traded contracts (张) — day-cumulative at the source, derived by subtracting consecutive samples.';
COMMENT ON COLUMN stats.options_intraday_5min.amount   IS 'Per-bar traded value (元) — day-cumulative at the source, derived by subtracting consecutive samples.';
COMMENT ON COLUMN stats.options_intraday_5min.exchange IS 'Venue of the contract (SSE for this stream; column kept aligned with the daily options_* tables).';

-- Cross-contract date scans (the code-first PK only serves per-contract paths)
CREATE INDEX IF NOT EXISTS idx_options_intraday_5min_date
    ON stats.options_intraday_5min (date);
