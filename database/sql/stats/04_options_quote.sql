-- ============================================================================
--  Options Quote - Split Tables
--  Original: options_quote table from schema.sql
--  Split into: options_identity, options_terms, options_strike, options_settlement,
--              options_greeks, options_volume_oi, options_aggregate
--  Reconstruct via: v_options_quote view (see 99_reconstruct_views.sql)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: options_identity
--   Identity core (PK) for all options_quote sub-tables
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.options_identity (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    contract_name             TEXT          NOT NULL,
    exchange                  TEXT          NOT NULL
        CONSTRAINT chk_options_identity_exchange
        CHECK (exchange IN ('SSE','SZSE','CFFEX')),

    CONSTRAINT pk_options_identity PRIMARY KEY (contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by contract_code
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_identity', 8);

COMMENT ON TABLE  stats.options_identity              IS 'Options identity: one row per (date, contract_code). PK (contract_code, date) shared by all options sub-tables. Native HASH partitioned by contract_code.';
COMMENT ON COLUMN stats.options_identity.contract_code IS 'SZSE option contract code (8-digit numeric string).';

-- ----------------------------------------------------------------------------
-- Table: options_terms
--   ← Underlying and contract terms
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.options_terms (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    underlying_code           TEXT          NOT NULL,
    underlying_name           TEXT          NOT NULL,
    underlying_target_type      TEXT          NOT NULL
        CHECK (underlying_target_type IN ('ETF','INDEX')),
    exchange                  TEXT          NOT NULL
        CHECK (exchange IN ('SZSE','SSE','CFFEX')),

    option_type               TEXT          NOT NULL
        CHECK (option_type IN ('CALL','PUT')),
    expiry_month              TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,
    days_to_expiry            INTEGER       NOT NULL DEFAULT 0,

    CONSTRAINT pk_options_terms PRIMARY KEY (contract_code, date),
    CONSTRAINT fk_options_terms_date_contract FOREIGN KEY (contract_code, date) REFERENCES stats.options_identity(contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_terms', 8);

COMMENT ON TABLE  stats.options_terms                 IS 'Options underlying and contract terms.';
COMMENT ON COLUMN stats.options_terms.underlying_code IS 'SZSE: native ETF code (e.g. "159901"). CFFEX: underlying index code (e.g. "000300"). Venues are separated by code space + underlying_target_type.';
COMMENT ON COLUMN stats.options_terms.option_type     IS 'CALL = 认购 (right to buy); PUT = 认沽 (right to sell).';
COMMENT ON COLUMN stats.options_terms.expiry_month    IS 'Chinese month label from contract name (e.g. "12月"); for display only — use expiry_date for date math.';

-- ----------------------------------------------------------------------------
-- Table: options_strike
--   ← Strike price data (in 厘 = 1/1000 yuan)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.options_strike (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    exchange                  TEXT          NOT NULL
        CONSTRAINT chk_options_strike_exchange
        CHECK (exchange IN ('SSE','SZSE','CFFEX')),
    strike_str                TEXT,
    strike_price_raw          NUMERIC(18,4),
    strike_price              NUMERIC(18,4) NOT NULL DEFAULT 0,
    has_a_suffix              SMALLINT      NOT NULL DEFAULT 0
        CHECK (has_a_suffix IN (0,1)),

    CONSTRAINT pk_options_strike PRIMARY KEY (contract_code, date),
    CONSTRAINT fk_options_strike_date_contract FOREIGN KEY (contract_code, date) REFERENCES stats.options_identity(contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_strike', 8);

COMMENT ON TABLE  stats.options_strike                IS 'Options strike price data (in 厘 = 1/1000 yuan).';
COMMENT ON COLUMN stats.options_strike.has_a_suffix   IS '1 if contract name carries "A" suffix (=contract adjusted for underlying split/dividend); 0 otherwise.';
COMMENT ON COLUMN stats.options_strike.strike_price   IS 'Normalized strike in 厘 (1/1000 yuan). Divide by 1000 for yuan.';

-- ----------------------------------------------------------------------------
-- Table: options_settlement
--   ← Daily settlement prices and moneyness
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.options_settlement (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    exchange                  TEXT          NOT NULL
        CONSTRAINT chk_options_settlement_exchange
        CHECK (exchange IN ('SSE','SZSE','CFFEX')),
    prev_settle               NUMERIC(18,4),
    close                     NUMERIC(18,4),
    settle                    NUMERIC(18,4),
    pct_change                NUMERIC(10,4),
    prev_settle_norm          NUMERIC(18,6),
    close_norm                NUMERIC(18,6),
    settle_norm               NUMERIC(18,6),
    underlying_close          NUMERIC(18,4) NOT NULL DEFAULT 0,
    moneyness_ratio           NUMERIC(12,8),

    CONSTRAINT pk_options_settlement PRIMARY KEY (contract_code, date),
    CONSTRAINT fk_options_settlement_date_contract FOREIGN KEY (contract_code, date) REFERENCES stats.options_identity(contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_settlement', 8);

COMMENT ON TABLE  stats.options_settlement            IS 'Options daily settlement prices and moneyness.';
COMMENT ON COLUMN stats.options_settlement.settle    IS 'Daily settlement price in 元/张 (yuan per contract).';

-- ----------------------------------------------------------------------------
-- Table: options_greeks
--   ← Greeks (per-contract, Black-Scholes model)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.options_greeks (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    exchange                  TEXT          NOT NULL
        CONSTRAINT chk_options_greeks_exchange
        CHECK (exchange IN ('SSE','SZSE','CFFEX')),
    implied_vol               NUMERIC(12,8),
    delta                     NUMERIC(18,8),
    theta                     NUMERIC(18,8),
    gamma                     NUMERIC(18,8),
    vega                      NUMERIC(18,8),
    rho                       NUMERIC(18,8),

    CONSTRAINT pk_options_greeks PRIMARY KEY (contract_code, date),
    CONSTRAINT fk_options_greeks_date_contract FOREIGN KEY (contract_code, date) REFERENCES stats.options_identity(contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_greeks', 8);

COMMENT ON TABLE  stats.options_greeks                IS 'Options Greeks (per-contract, Black-Scholes model).';
COMMENT ON COLUMN stats.options_greeks.implied_vol   IS 'Black-Scholes implied vol (decimal, not %). NULL if computation failed.';

-- ----------------------------------------------------------------------------
-- Table: options_volume_oi
--   ← Volume & open interest (contracts)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.options_volume_oi (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    exchange                  TEXT          NOT NULL
        CONSTRAINT chk_options_volume_oi_exchange
        CHECK (exchange IN ('SSE','SZSE','CFFEX')),
    volume                    NUMERIC(24,4) NOT NULL DEFAULT 0,
    volume_wan                NUMERIC(24,4) NOT NULL DEFAULT 0,
    open_interest             NUMERIC(24,4) NOT NULL DEFAULT 0,
    open_interest_wan         NUMERIC(24,4) NOT NULL DEFAULT 0,

    CONSTRAINT pk_options_volume_oi PRIMARY KEY (contract_code, date),
    CONSTRAINT fk_options_volume_oi_date_contract FOREIGN KEY (contract_code, date) REFERENCES stats.options_identity(contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_volume_oi', 8);

COMMENT ON TABLE  stats.options_volume_oi             IS 'Options volume & open interest (contracts).';
COMMENT ON COLUMN stats.options_volume_oi.open_interest IS 'Open interest at end of day (contracts). Frontend uses this for call/put walls and max-pain.';

-- ----------------------------------------------------------------------------
-- Table: options_aggregate
--   ← Per-underlying aggregate context
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.options_aggregate (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    exchange                  TEXT          NOT NULL
        CONSTRAINT chk_options_aggregate_exchange
        CHECK (exchange IN ('SSE','SZSE','CFFEX')),
    total_volume_underlying   NUMERIC(24,4),
    total_oi_underlying       NUMERIC(24,4),
    volume_pct                NUMERIC(10,6),
    open_interest_pct         NUMERIC(10,6),
    oi_call_put_ratio         NUMERIC(18,8),
    vol_call_put_ratio        NUMERIC(18,8),
    open_interest_call        NUMERIC(24,4),
    open_interest_put         NUMERIC(24,4),
    volume_call               NUMERIC(24,4),
    volume_put                NUMERIC(24,4),
    oi_total_call_put_ratio   NUMERIC(18,8),

    CONSTRAINT pk_options_aggregate PRIMARY KEY (contract_code, date),
    CONSTRAINT fk_options_aggregate_date_contract FOREIGN KEY (contract_code, date) REFERENCES stats.options_identity(contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_aggregate', 8);

COMMENT ON TABLE  stats.options_aggregate            IS 'Options per-underlying aggregate context (one row per (underlying_code, date)).';

-- Indexes
-- Underlying-code lookups on options_terms (non-PK key path).
CREATE INDEX IF NOT EXISTS idx_options_quote_underlying_date
    ON stats.options_terms (underlying_code, date);

CREATE INDEX IF NOT EXISTS idx_options_quote_underlying_date_expiry
    ON stats.options_terms (underlying_code, date, expiry_date);

-- The legacy (contract_code, date) secondary indexes are superseded by
-- the code-first PK; the date-first indexes below restore the
-- cross-contract date scans the old date-first PK used to serve.

CREATE INDEX IF NOT EXISTS idx_options_identity_date
    ON stats.options_identity (date);
CREATE INDEX IF NOT EXISTS idx_options_strike_date
    ON stats.options_strike (date);
CREATE INDEX IF NOT EXISTS idx_options_settlement_date
    ON stats.options_settlement (date);
CREATE INDEX IF NOT EXISTS idx_options_greeks_date
    ON stats.options_greeks (date);
CREATE INDEX IF NOT EXISTS idx_options_volume_oi_date
    ON stats.options_volume_oi (date);
CREATE INDEX IF NOT EXISTS idx_options_aggregate_date
    ON stats.options_aggregate (date);

-- ----------------------------------------------------------------------------
-- Migration (idempotent): exchange on EVERY options_* table
--   04 originally carried exchange only on options_terms. It now lives on all
--   7 tables (see CREATE TABLEs above) so venue filters/joins (SZSE vs CFFEX
--   vs SSE missing-date detection, future SSE loader) hit the table directly.
--   Existing rows are backfilled from options_terms — every identity row has
--   a terms row (FK + verified in prod), so the SET NOT NULL below is safe;
--   it fails loudly if that invariant ever regresses.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'options_identity', 'options_strike', 'options_settlement',
        'options_greeks', 'options_volume_oi', 'options_aggregate'
    ] LOOP
        EXECUTE format('ALTER TABLE stats.%I ADD COLUMN IF NOT EXISTS exchange TEXT', tbl);
    END LOOP;
END $$;

COMMENT ON COLUMN stats.options_identity.exchange IS 'Venue of the contract: SSE / SZSE / CFFEX. Same value as options_terms.exchange, denormalized onto every options_* table so venue filters need no join.';

UPDATE stats.options_identity x SET exchange = t.exchange
  FROM stats.options_terms t
 WHERE x.contract_code = t.contract_code AND x.date = t.date
   AND (x.exchange IS NULL OR x.exchange = '');
UPDATE stats.options_strike x SET exchange = t.exchange
  FROM stats.options_terms t
 WHERE x.contract_code = t.contract_code AND x.date = t.date
   AND (x.exchange IS NULL OR x.exchange = '');
UPDATE stats.options_settlement x SET exchange = t.exchange
  FROM stats.options_terms t
 WHERE x.contract_code = t.contract_code AND x.date = t.date
   AND (x.exchange IS NULL OR x.exchange = '');
UPDATE stats.options_greeks x SET exchange = t.exchange
  FROM stats.options_terms t
 WHERE x.contract_code = t.contract_code AND x.date = t.date
   AND (x.exchange IS NULL OR x.exchange = '');
UPDATE stats.options_volume_oi x SET exchange = t.exchange
  FROM stats.options_terms t
 WHERE x.contract_code = t.contract_code AND x.date = t.date
   AND (x.exchange IS NULL OR x.exchange = '');
UPDATE stats.options_aggregate x SET exchange = t.exchange
  FROM stats.options_terms t
 WHERE x.contract_code = t.contract_code AND x.date = t.date
   AND (x.exchange IS NULL OR x.exchange = '');

ALTER TABLE stats.options_identity  ALTER COLUMN exchange SET NOT NULL;
ALTER TABLE stats.options_strike    ALTER COLUMN exchange SET NOT NULL;
ALTER TABLE stats.options_settlement ALTER COLUMN exchange SET NOT NULL;
ALTER TABLE stats.options_greeks    ALTER COLUMN exchange SET NOT NULL;
ALTER TABLE stats.options_volume_oi ALTER COLUMN exchange SET NOT NULL;
ALTER TABLE stats.options_aggregate ALTER COLUMN exchange SET NOT NULL;

-- CHECK constraints via the shared guarded helper (NOT VALID, then validated
-- schema-wide below) — naming must keep the chk_ prefix for the validator.
SELECT public.ensure_check_constraint('stats.options_identity',   'chk_options_identity_exchange',   'exchange IN (''SSE'',''SZSE'',''CFFEX'')');
SELECT public.ensure_check_constraint('stats.options_strike',     'chk_options_strike_exchange',     'exchange IN (''SSE'',''SZSE'',''CFFEX'')');
SELECT public.ensure_check_constraint('stats.options_settlement', 'chk_options_settlement_exchange', 'exchange IN (''SSE'',''SZSE'',''CFFEX'')');
SELECT public.ensure_check_constraint('stats.options_greeks',     'chk_options_greeks_exchange',     'exchange IN (''SSE'',''SZSE'',''CFFEX'')');
SELECT public.ensure_check_constraint('stats.options_volume_oi',  'chk_options_volume_oi_exchange',  'exchange IN (''SSE'',''SZSE'',''CFFEX'')');
SELECT public.ensure_check_constraint('stats.options_aggregate',  'chk_options_aggregate_exchange',  'exchange IN (''SSE'',''SZSE'',''CFFEX'')');
SELECT public.validate_pending_checks('stats');
