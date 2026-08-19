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

    CONSTRAINT pk_options_identity PRIMARY KEY (date, contract_code)
);

COMMENT ON TABLE  stats.options_identity              IS 'Options identity: one row per (date, contract_code). PK shared by all options sub-tables.';
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

    CONSTRAINT pk_options_terms PRIMARY KEY (date, contract_code),
    CONSTRAINT fk_options_terms_date_contract FOREIGN KEY (date, contract_code) REFERENCES stats.options_identity(date, contract_code)
);

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
    strike_str                TEXT,
    strike_price_raw          NUMERIC(18,4),
    strike_price              NUMERIC(18,4) NOT NULL DEFAULT 0,
    has_a_suffix              SMALLINT      NOT NULL DEFAULT 0
        CHECK (has_a_suffix IN (0,1)),

    CONSTRAINT pk_options_strike PRIMARY KEY (date, contract_code),
    CONSTRAINT fk_options_strike_date_contract FOREIGN KEY (date, contract_code) REFERENCES stats.options_identity(date, contract_code)
);

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
    prev_settle               NUMERIC(18,4),
    close                     NUMERIC(18,4),
    settle                    NUMERIC(18,4),
    pct_change                NUMERIC(10,4),
    prev_settle_norm          NUMERIC(18,6),
    close_norm                NUMERIC(18,6),
    settle_norm               NUMERIC(18,6),
    underlying_close          NUMERIC(18,4) NOT NULL DEFAULT 0,
    moneyness_ratio           NUMERIC(12,8),

    CONSTRAINT pk_options_settlement PRIMARY KEY (date, contract_code),
    CONSTRAINT fk_options_settlement_date_contract FOREIGN KEY (date, contract_code) REFERENCES stats.options_identity(date, contract_code)
);

COMMENT ON TABLE  stats.options_settlement            IS 'Options daily settlement prices and moneyness.';
COMMENT ON COLUMN stats.options_settlement.settle    IS 'Daily settlement price in 元/张 (yuan per contract).';

-- ----------------------------------------------------------------------------
-- Table: options_greeks
--   ← Greeks (per-contract, Black-Scholes model)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.options_greeks (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    implied_vol               NUMERIC(12,8),
    delta                     NUMERIC(18,8),
    theta                     NUMERIC(18,8),
    gamma                     NUMERIC(18,8),
    vega                      NUMERIC(18,8),
    rho                       NUMERIC(18,8),

    CONSTRAINT pk_options_greeks PRIMARY KEY (date, contract_code),
    CONSTRAINT fk_options_greeks_date_contract FOREIGN KEY (date, contract_code) REFERENCES stats.options_identity(date, contract_code)
);

COMMENT ON TABLE  stats.options_greeks                IS 'Options Greeks (per-contract, Black-Scholes model).';
COMMENT ON COLUMN stats.options_greeks.implied_vol   IS 'Black-Scholes implied vol (decimal, not %). NULL if computation failed.';

-- ----------------------------------------------------------------------------
-- Table: options_volume_oi
--   ← Volume & open interest (contracts)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.options_volume_oi (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    volume                    NUMERIC(24,4) NOT NULL DEFAULT 0,
    volume_wan                NUMERIC(24,4) NOT NULL DEFAULT 0,
    open_interest             NUMERIC(24,4) NOT NULL DEFAULT 0,
    open_interest_wan         NUMERIC(24,4) NOT NULL DEFAULT 0,

    CONSTRAINT pk_options_volume_oi PRIMARY KEY (date, contract_code),
    CONSTRAINT fk_options_volume_oi_date_contract FOREIGN KEY (date, contract_code) REFERENCES stats.options_identity(date, contract_code)
);

COMMENT ON TABLE  stats.options_volume_oi             IS 'Options volume & open interest (contracts).';
COMMENT ON COLUMN stats.options_volume_oi.open_interest IS 'Open interest at end of day (contracts). Frontend uses this for call/put walls and max-pain.';

-- ----------------------------------------------------------------------------
-- Table: options_aggregate
--   ← Per-underlying aggregate context
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.options_aggregate (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
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

    CONSTRAINT pk_options_aggregate PRIMARY KEY (date, contract_code),
    CONSTRAINT fk_options_aggregate_date_contract FOREIGN KEY (date, contract_code) REFERENCES stats.options_identity(date, contract_code)
);

COMMENT ON TABLE  stats.options_aggregate            IS 'Options per-underlying aggregate context (one row per (underlying_code, date)).';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_options_quote_underlying_date
    ON stats.options_terms (underlying_code, date);

CREATE INDEX IF NOT EXISTS idx_options_quote_underlying_date_expiry
    ON stats.options_terms (underlying_code, date, expiry_date);

CREATE INDEX IF NOT EXISTS idx_options_quote_contract_date
    ON stats.options_identity (contract_code, date);

CREATE INDEX IF NOT EXISTS idx_options_strike_contract_date
    ON stats.options_strike (contract_code, date);

CREATE INDEX IF NOT EXISTS idx_options_settlement_contract_date
    ON stats.options_settlement (contract_code, date);

CREATE INDEX IF NOT EXISTS idx_options_greeks_contract_date
    ON stats.options_greeks (contract_code, date);

CREATE INDEX IF NOT EXISTS idx_options_volume_oi_contract_date
    ON stats.options_volume_oi (contract_code, date);

CREATE INDEX IF NOT EXISTS idx_options_aggregate_contract_date
    ON stats.options_aggregate (contract_code, date);
