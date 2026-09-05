-- ============================================================================
--  Options Expiry Identity — lookup table for (date, option_type,
--  underlying_code, expiry_date) groups. All other options analysis
--  tables have FK references to this table.
--
--  Table: analysis.options_expiry_identity
--    PK: (date, option_type, underlying_code, expiry_date)
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis.options_expiry_identity (
    date                      DATE          NOT NULL,
    option_type               TEXT          NOT NULL
        CHECK (option_type IN ('CALL','PUT')),
    underlying_code           TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,

    CONSTRAINT pk_options_expiry_identity
        PRIMARY KEY (underlying_code, date, option_type, expiry_date)
) PARTITION BY HASH (underlying_code);

-- Native hash partitions (8) keyed by underlying_code
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'options_expiry_identity', 8);

CREATE TABLE IF NOT EXISTS analysis.options_skewness_stats (
    date                      DATE          NOT NULL,
    option_type               TEXT          NOT NULL
        CHECK (option_type IN ('CALL','PUT')),
    underlying_code           TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,
    skew_type                 TEXT          NOT NULL
        CHECK (skew_type IN ('oi_moneyness','iv_smile',
                             'greek_delta','greek_gamma','greek_vega')),

    skewness                  NUMERIC(10,4),
    count_skewness_curve_crossed_spot INTEGER           NOT NULL DEFAULT 0, -- count of times the gap (skewness - neutral) changed sign, accumulated per expiry group

    skewness_ma5                NUMERIC(10,4),
    skewness_ma20               NUMERIC(10,4),
    skewness_ma60               NUMERIC(10,4),

    skewness_std5               NUMERIC(10,4),
    skewness_std20              NUMERIC(10,4),
    skewness_std60              NUMERIC(10,4),

    gap_skewness_vs_spot_ma5    NUMERIC(10,4),
    gap_skewness_vs_spot_ma20   NUMERIC(10,4),
    gap_skewness_vs_spot_ma60   NUMERIC(10,4),

    gap_skewness_vs_spot_slope  NUMERIC(10,4),
    gap_skewness_vs_spot_ma5_slope   NUMERIC(10,4),
    gap_skewness_vs_spot_ma20_slope  NUMERIC(10,4),
    gap_skewness_vs_spot_ma60_slope  NUMERIC(10,4),

    corr_skewness_ma5_vs_spot_ma5       NUMERIC(10,4),
    corr_skewness_ma20_vs_spot_ma20       NUMERIC(10,4),
    corr_skewness_ma60_vs_spot_ma60       NUMERIC(10,4),

    CONSTRAINT pk_options_skewness_stats
        PRIMARY KEY (underlying_code, date, option_type, expiry_date, skew_type),
    CONSTRAINT fk_options_skewness_stats_expiry
        FOREIGN KEY (underlying_code, date, option_type, expiry_date)
        REFERENCES analysis.options_expiry_identity
            (underlying_code, date, option_type, expiry_date)
) PARTITION BY HASH (underlying_code);

-- Native hash partitions (8) keyed by underlying_code
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'options_skewness_stats', 8);

-- Indexes for common access patterns:
--   1. Per-underlying time series (panel loads one underlying's history).
--   2. Per-expiry scan (all dates of one expiry group).
-- idx_options_skewness_stats_underlying_date (underlying_code, date) dropped:
-- a prefix of the underlying_code-first PK, which already serves per-underlying lookups.
DROP INDEX IF EXISTS analysis.idx_options_skewness_stats_underlying_date;

CREATE INDEX IF NOT EXISTS idx_options_skewness_stats_expiry
    ON analysis.options_skewness_stats (underlying_code, expiry_date, date);

COMMENT ON TABLE  analysis.options_skewness_stats              IS 'Per-(underlying_code, date, option_type, expiry_date, skew_type) store of precomputed rolling skewness statistics for option expiry groups, for multiple skew data sources separated by skew_type: oi_moneyness = OI-weighted mean moneyness (strike_price / underlying_close) — a positioning metric; iv_smile = OI-weighted 3rd standardized moment of implied vol across strikes (from stats.options_greeks) — a pricing metric; greek_delta = delta-weighted put/call OI ratio dpcr (whole chain, neutral 0.5); greek_gamma = normalized GEX-style call-minus-put gamma balance (whole chain, neutral 0); greek_vega = OTM-wing vega balance (0<|delta|<0.5 wings, neutral 0 — the open-interest mirror of the 25d risk reversal). The greek_* metrics are PAIR-level CALL-vs-PUT contrasts (CALL and PUT rows of a pair hold the SAME value), weighted by open_interest with zero OI = zero vote; theta/rho have no industry-standard positioning skew and are not computed. Rolling windows (5/20/60 days) compute MA, STD, gap-from-neutral (skewness_MA − neutral; neutral = 1 / 0.5 / 0 by type), slope of gap, and correlation with spot (price-space basis: underlying_close × skewness for oi_moneyness/iv_smile, underlying_close × (1 + (skewness − neutral) × 0.10) for greek_*). FK -> analysis.options_expiry_identity. Built by analyze.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_skewness_stats.date                    IS 'Trading date.';
COMMENT ON COLUMN analysis.options_skewness_stats.option_type            IS 'Option type: CALL or PUT. For pair-level metrics (greek_*, and the OI/IV-skew tables) the CALL and PUT rows of the same group hold the SAME value.';
COMMENT ON COLUMN analysis.options_skewness_stats.underlying_code        IS 'Underlying code (unified index codes; SZSE ETF options mapped via ETF->Index, e.g. 159919->000300).';
COMMENT ON COLUMN analysis.options_skewness_stats.expiry_date            IS 'Exact contract expiry date. CFFEX (3rd Friday) and SZSE (4th Wednesday) expiries differ within a month.';
COMMENT ON COLUMN analysis.options_skewness_stats.skew_type              IS 'Data source of the skew metric: oi_moneyness = OI-weighted mean moneyness (strike/spot, positioning); iv_smile = OI-weighted 3rd moment of implied vol across strikes (pricing, from stats.options_greeks); greek_delta = delta-weighted put/call OI ratio dpcr (whole chain, neutral 0.5 — the delta-weighted refinement of the plain put/call ratio); greek_gamma = normalized GEX-style call-minus-put gamma balance (whole chain, neutral 0; call gamma positive / put gamma negative per the dealer-positioning sign convention); greek_vega = OTM-wing vega balance (calls 0<delta<0.5 vs puts -0.5<delta<0, neutral 0 — the open-interest mirror of the 25d risk reversal). greek_* rows are PAIR-level: the CALL and PUT rows of the same (date, underlying, expiry) hold the SAME value. greek_theta/greek_rho removed (no industry-standard positioning skew).';
COMMENT ON COLUMN analysis.options_skewness_stats.skewness                IS 'Daily raw value of the skew_type''s metric (all skewness_ma*/std* columns are rolling stats of it): oi_moneyness = OI-wtd mean moneyness (K/S); iv_smile = OI-wtd 3rd moment of IV; greek_delta = delta-wtd put/call OI ratio in [0,1] (neutral 0.5); greek_gamma / greek_vega = call-vs-put balances in [-1,1] (neutral 0). Gap columns are measured from the type''s neutral anchor (1 / 0.5 / 0).';
COMMENT ON COLUMN analysis.options_skewness_stats.skewness_ma5            IS '5-day rolling MA of the skew_type''s daily metric across the expiry group''s days.';
COMMENT ON COLUMN analysis.options_skewness_stats.skewness_ma20           IS '20-day rolling MA of the skew_type''s daily metric.';
COMMENT ON COLUMN analysis.options_skewness_stats.skewness_ma60           IS '60-day rolling MA of the skew_type''s daily metric.';
COMMENT ON COLUMN analysis.options_skewness_stats.skewness_std5          IS '5-day rolling STD of the skew_type''s daily metric.';
COMMENT ON COLUMN analysis.options_skewness_stats.skewness_std20         IS '20-day rolling STD of the skew_type''s daily metric.';
COMMENT ON COLUMN analysis.options_skewness_stats.skewness_std60         IS '60-day rolling STD of the skew_type''s daily metric.';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_skewness_vs_spot_ma5   IS '5-day rolling gap from the type''s neutral anchor (skewness_ma5 - neutral; neutral = 1 oi_moneyness/iv_smile, 0.5 greek_delta, 0 greek_gamma/vega).';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_skewness_vs_spot_ma20  IS '20-day rolling gap from the type''s neutral anchor (skewness_ma20 - neutral).';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_skewness_vs_spot_ma60  IS '60-day rolling gap from the type''s neutral anchor (skewness_ma60 - neutral).';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_skewness_vs_spot_slope       IS 'Linear regression slope of (skewness - neutral) vs time over full history; trend of the gap.';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_skewness_vs_spot_ma5_slope   IS 'Linear regression slope of gap_skewness_vs_spot_ma5 vs time.';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_skewness_vs_spot_ma20_slope  IS 'Linear regression slope of gap_skewness_vs_spot_ma20 vs time.';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_skewness_vs_spot_ma60_slope  IS 'Linear regression slope of gap_skewness_vs_spot_ma60 vs time.';
COMMENT ON COLUMN analysis.options_skewness_stats.corr_skewness_ma5_vs_spot_ma5 IS 'Whole-period correlation between MA5 of skew_price (price space) and MA5 of spot price, cumulative since first date of expiry group. Price-space basis: underlying_close × skewness (oi_moneyness/iv_smile) or underlying_close × (1 + (skewness − neutral) × 0.10) (greek_*).';
COMMENT ON COLUMN analysis.options_skewness_stats.corr_skewness_ma20_vs_spot_ma20 IS 'Whole-period correlation between MA20 of skew_price (price space) and MA20 of spot price, cumulative since first date of expiry group.';
COMMENT ON COLUMN analysis.options_skewness_stats.corr_skewness_ma60_vs_spot_ma60 IS 'Whole-period correlation between MA60 of skew_price (price space) and MA60 of spot price, cumulative since first date of expiry group.';
COMMENT ON COLUMN analysis.options_skewness_stats.count_skewness_curve_crossed_spot IS 'Cumulative count of sign changes in the gap (skewness − the type''s neutral anchor) for this expiry group. Increments when the gap crosses from below-neutral (negative) to at/above-neutral (non-negative), or vice versa. First day of each expiry group = 0; on each subsequent day, if the sign changed from the previous day the counter increments, otherwise it keeps the previous value.';

-- Migration for pre-existing tables (idempotent): add skew_type + rebuild PK.
ALTER TABLE analysis.options_skewness_stats
    ADD COLUMN IF NOT EXISTS skew_type TEXT NOT NULL DEFAULT 'oi_moneyness';
UPDATE analysis.options_skewness_stats
    SET skew_type = 'oi_moneyness' WHERE skew_type IS NULL;
-- Daily raw skewness value (the MA5/20/60 columns are rolling stats of it);
-- needed by the DB-driven charts (greek_* types). Widened to 4 decimals for
-- the greek_* ratio metrics. Superset constraint: drop + re-add is
-- idempotent and safe (existing values remain valid).
ALTER TABLE analysis.options_skewness_stats
    ADD COLUMN IF NOT EXISTS skewness NUMERIC(10,4);
-- Per-greek skew redesign: widen value columns to 4 decimals (greek_*
-- ratios die at 2 decimals near their neutral anchor), purge legacy
-- greek_* rows (old per-side ATM-normalized centroid semantics, incl. the
-- removed greek_theta/greek_rho) so the incremental pipeline recomputes
-- them with the new PAIR-level metrics, then rebuild the CHECK constraint.
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN skewness TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN skewness_ma5 TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN skewness_ma20 TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN skewness_ma60 TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN skewness_std5 TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN skewness_std20 TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN skewness_std60 TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN gap_skewness_vs_spot_ma5 TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN gap_skewness_vs_spot_ma20 TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN gap_skewness_vs_spot_ma60 TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN gap_skewness_vs_spot_slope TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN gap_skewness_vs_spot_ma5_slope TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN gap_skewness_vs_spot_ma20_slope TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN gap_skewness_vs_spot_ma60_slope TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN corr_skewness_ma5_vs_spot_ma5 TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN corr_skewness_ma20_vs_spot_ma20 TYPE NUMERIC(10,4);
ALTER TABLE analysis.options_skewness_stats
    ALTER COLUMN corr_skewness_ma60_vs_spot_ma60 TYPE NUMERIC(10,4);
DELETE FROM analysis.options_skewness_stats
    WHERE skew_type LIKE 'greek_%';
ALTER TABLE analysis.options_skewness_stats
    DROP CONSTRAINT IF EXISTS ck_options_skewness_stats_skew_type;
ALTER TABLE analysis.options_skewness_stats
    ADD CONSTRAINT ck_options_skewness_stats_skew_type
        CHECK (skew_type IN ('oi_moneyness','iv_smile',
                             'greek_delta','greek_gamma','greek_vega'));
ALTER TABLE analysis.options_skewness_stats
    DROP CONSTRAINT IF EXISTS pk_options_skewness_stats;
ALTER TABLE analysis.options_skewness_stats
    ADD CONSTRAINT pk_options_skewness_stats
        PRIMARY KEY (underlying_code, date, option_type, expiry_date, skew_type);

CREATE TABLE IF NOT EXISTS analysis.options_oi_stats (
    date                      DATE          NOT NULL,
    option_type               TEXT          NOT NULL
        CHECK (option_type IN ('CALL','PUT')),
    underlying_code           TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,

    corr_put_call_ratio_vs_spot_ma5  NUMERIC(10,2),
    corr_put_call_ratio_vs_spot_ma20  NUMERIC(10,2),
    corr_put_call_ratio_vs_spot_ma60  NUMERIC(10,2),

    CONSTRAINT pk_options_oi_stats
        PRIMARY KEY (underlying_code, date, option_type, expiry_date),
    CONSTRAINT fk_options_oi_stats_expiry
        FOREIGN KEY (underlying_code, date, option_type, expiry_date)
        REFERENCES analysis.options_expiry_identity
            (underlying_code, date, option_type, expiry_date)
) PARTITION BY HASH (underlying_code);

-- Native hash partitions (8) keyed by underlying_code
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'options_oi_stats', 8);

-- idx_options_oi_stats_underlying_date (underlying_code, date) dropped:
-- a prefix of the underlying_code-first PK, which already serves per-underlying lookups.
DROP INDEX IF EXISTS analysis.idx_options_oi_stats_underlying_date;

CREATE INDEX IF NOT EXISTS idx_options_oi_stats_expiry
    ON analysis.options_oi_stats (underlying_code, expiry_date, date);

COMMENT ON TABLE  analysis.options_oi_stats                       IS 'Per-(underlying_code, date, option_type, expiry_date) store of precomputed options OI-related statistics for expiry groups. Stores MA5/MA20/MA60 whole-period cumulative correlation between put/call OI ratio and underlying spot price. FK -> analysis.options_expiry_identity. Built by analyze.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_oi_stats.date                    IS 'Trading date.';
COMMENT ON COLUMN analysis.options_oi_stats.option_type            IS 'Option type: CALL or PUT.';
COMMENT ON COLUMN analysis.options_oi_stats.underlying_code         IS 'Underlying code (unified index codes; SZSE ETF options mapped via ETF->Index).';
COMMENT ON COLUMN analysis.options_oi_stats.expiry_date             IS 'Exact contract expiry date.';
COMMENT ON COLUMN analysis.options_oi_stats.corr_put_call_ratio_vs_spot_ma5 IS 'Whole-period cumulative Pearson correlation between 5-day MA of put/call OI ratio and 5-day MA of underlying spot price for this expiry group.';
COMMENT ON COLUMN analysis.options_oi_stats.corr_put_call_ratio_vs_spot_ma20 IS 'Whole-period cumulative correlation between MA20 of put/call OI ratio and MA20 of spot price.';
COMMENT ON COLUMN analysis.options_oi_stats.corr_put_call_ratio_vs_spot_ma60 IS 'Whole-period cumulative correlation between MA60 of put/call OI ratio and MA60 of spot price.';

CREATE TABLE IF NOT EXISTS analysis.options_iv_skew_stats (
    date                      DATE          NOT NULL,
    option_type               TEXT          NOT NULL
        CHECK (option_type IN ('CALL','PUT')),
    underlying_code           TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,

    atm_iv                      NUMERIC(10,2),   -- IV (vol pts %) of contract closest to moneyness 1.0
    iv_call25                   NUMERIC(10,2),   -- IV (vol pts %) of OTM CALL nearest |delta| = 0.25
    iv_put25                    NUMERIC(10,2),   -- IV (vol pts %) of OTM PUT nearest |delta| = 0.25
    risk_reversal_25d           NUMERIC(10,2),   -- iv_call25 - iv_put25 (negative = puts richer)
    put_skew_25d                NUMERIC(10,2),   -- iv_put25 - atm_iv
    call_skew_25d               NUMERIC(10,2),   -- iv_call25 - atm_iv
    smile_skewness              NUMERIC(10,2),   -- OI-weighted 3rd moment of IV across strikes (per option_type)

    rr25_ma5                     NUMERIC(10,2),
    rr25_ma20                    NUMERIC(10,2),
    rr25_ma60                    NUMERIC(10,2),

    rr25_std5                    NUMERIC(10,2),
    rr25_std20                   NUMERIC(10,2),
    rr25_std60                   NUMERIC(10,2),

    rr25_slope                   NUMERIC(10,2),
    rr25_ma5_slope               NUMERIC(10,2),
    rr25_ma20_slope              NUMERIC(10,2),
    rr25_ma60_slope              NUMERIC(10,2),

    corr_rr25_ma5_vs_spot_ma5    NUMERIC(10,2),
    corr_rr25_ma20_vs_spot_ma20  NUMERIC(10,2),
    corr_rr25_ma60_vs_spot_ma60  NUMERIC(10,2),

    CONSTRAINT pk_options_iv_skew_stats
        PRIMARY KEY (underlying_code, date, option_type, expiry_date),
    CONSTRAINT fk_options_iv_skew_stats_expiry
        FOREIGN KEY (underlying_code, date, option_type, expiry_date)
        REFERENCES analysis.options_expiry_identity
            (underlying_code, date, option_type, expiry_date)
) PARTITION BY HASH (underlying_code);

-- Native hash partitions (8) keyed by underlying_code
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'options_iv_skew_stats', 8);

-- idx_options_iv_skew_stats_underlying_date (underlying_code, date) dropped:
-- a prefix of the underlying_code-first PK, which already serves per-underlying lookups.
DROP INDEX IF EXISTS analysis.idx_options_iv_skew_stats_underlying_date;

CREATE INDEX IF NOT EXISTS idx_options_iv_skew_stats_expiry
    ON analysis.options_iv_skew_stats (underlying_code, expiry_date, date);

COMMENT ON TABLE  analysis.options_iv_skew_stats              IS 'Per-(underlying_code, date, option_type, expiry_date) store of implied-volatility skew statistics for option expiry groups, derived from implied_vol in stats.options_greeks (calibrated from option premiums via Black-76). All IV/skew values are in vol points (percent). Unlike options_skewness_stats (OI-weighted mean moneyness — a positioning metric), this is a pricing metric. FK -> analysis.options_expiry_identity. Built by analyze.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.atm_iv       IS 'IV (vol points, %) of the contract with moneyness (strike/spot) closest to 1.0 in the expiry group.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.iv_call25    IS 'IV (vol points, %) of the OTM CALL contract with delta nearest 0.25.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.iv_put25     IS 'IV (vol points, %) of the OTM PUT contract with delta nearest -0.25.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.risk_reversal_25d IS '25-delta risk reversal: iv_call25 - iv_put25 (vol points). Negative = OTM puts richer than OTM calls = downside hedging demand.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.put_skew_25d  IS 'iv_put25 - atm_iv (vol points); premium paid for downside protection.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.call_skew_25d IS 'iv_call25 - atm_iv (vol points); upside speculation premium.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.smile_skewness IS 'OI-weighted 3rd standardized moment of IV across the expiry group''s strikes, per option_type. Negative = higher IV on downside.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_ma5     IS '5-day rolling MA of risk_reversal_25d.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_ma20    IS '20-day rolling MA of risk_reversal_25d.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_ma60    IS '60-day rolling MA of risk_reversal_25d.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_std5    IS '5-day rolling STD of risk_reversal_25d.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_std20   IS '20-day rolling STD of risk_reversal_25d.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_std60   IS '60-day rolling STD of risk_reversal_25d.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_slope   IS 'Linear regression slope of risk_reversal_25d vs time over full history of the expiry group.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_ma5_slope   IS 'Linear regression slope of rr25_ma5 vs time.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_ma20_slope  IS 'Linear regression slope of rr25_ma20 vs time.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_ma60_slope  IS 'Linear regression slope of rr25_ma60 vs time.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.corr_rr25_ma5_vs_spot_ma5   IS 'Whole-period cumulative correlation between MA5 of risk_reversal_25d and MA5 of spot price.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.corr_rr25_ma20_vs_spot_ma20 IS 'Whole-period cumulative correlation between MA20 of risk_reversal_25d and MA20 of spot price.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.corr_rr25_ma60_vs_spot_ma60 IS 'Whole-period cumulative correlation between MA60 of risk_reversal_25d and MA60 of spot price.';

CREATE TABLE IF NOT EXISTS analysis.options_walls (
    date                      DATE          NOT NULL,
    option_type               TEXT          NOT NULL
        CHECK (option_type IN ('CALL','PUT')),
    underlying_code           TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,
    wall_type                 TEXT          NOT NULL
        CHECK (wall_type = 'zone'),
    wall_strike               NUMERIC(12,4),   -- OI-weighted zone center in legacy PRICE_SCALE units (raw strike / 10000)
    wall_oi                   NUMERIC(14,2),   -- total OI of the dominant zone
    mean_oi                   NUMERIC(14,2),   -- unused (legacy 80pct/large_num columns, always NULL)
    threshold                 NUMERIC(8,4),    -- threshold value (zone: 0.06 mass share)

    -- wall_type='zone' (strength-scored OI wall zone with lifecycle):
    -- all price columns are in RAW strike units (same scale as
    -- stats.options_strike / stats.options_settlement.underlying_close).
    wall_low                  NUMERIC(12,4),   -- zone low strike (raw units)
    wall_high                 NUMERIC(12,4),   -- zone high strike (raw units)
    wall_center               NUMERIC(12,4),   -- OI-weighted center strike of the zone (raw units)
    mass_share                NUMERIC(8,6),    -- zone OI / total chain OI (call+put), 0..1
    gap_pct                   NUMERIC(10,4),   -- |center - spot| / spot * 100 (signed away-from-spot; NULL when spot missing)
    days_persisted            INTEGER,         -- consecutive trading days the zone has existed (>=50% strike-range overlap day-over-day)
    state                     TEXT
        CHECK (state IN ('ACTIVE','ERODED','BREACHED')),
    strength_score            NUMERIC(10,6),   -- mass_share * exp(-gap_pct/8) * (1 + 0.25*min(days_persisted,20)/20)

    CONSTRAINT pk_options_walls
        PRIMARY KEY (underlying_code, date, option_type, expiry_date, wall_type),
    CONSTRAINT fk_options_walls_expiry
        FOREIGN KEY (underlying_code, date, option_type, expiry_date)
        REFERENCES analysis.options_expiry_identity
            (underlying_code, date, option_type, expiry_date)
) PARTITION BY HASH (underlying_code);

-- Native hash partitions (8) keyed by underlying_code
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'options_walls', 8);

-- idx_options_walls_underlying_date (underlying_code, date) dropped:
-- a prefix of the underlying_code-first PK, which already serves per-underlying lookups.
DROP INDEX IF EXISTS analysis.idx_options_walls_underlying_date;

CREATE INDEX IF NOT EXISTS idx_options_walls_expiry
    ON analysis.options_walls (underlying_code, expiry_date, date);

COMMENT ON TABLE  analysis.options_walls                       IS 'Per-(underlying_code, date, option_type, expiry_date, wall_type) store of precomputed options wall levels. Single wall type: zone (strength-scored OI wall ZONE with lifecycle — see column comments). For CALL walls the wall acts as resistance/cap, for PUT walls as support/floor. FK -> analysis.options_expiry_identity. Built by analyze.options.';
COMMENT ON COLUMN analysis.options_walls.date                  IS 'Trading date.';
COMMENT ON COLUMN analysis.options_walls.option_type           IS 'Option type: CALL or PUT.';
COMMENT ON COLUMN analysis.options_walls.underlying_code       IS 'Underlying code (unified index codes).';
COMMENT ON COLUMN analysis.options_walls.expiry_date           IS 'Exact contract expiry date.';
COMMENT ON COLUMN analysis.options_walls.wall_type             IS 'Wall computation method: zone (dominant adjacent-strike OI cluster, strength-scored with lifecycle).';
COMMENT ON COLUMN analysis.options_walls.wall_strike           IS 'OI-weighted zone center in legacy PRICE_SCALE units (raw strike / 10000). Raw-unit comparisons should use wall_low/wall_high/wall_center.';
COMMENT ON COLUMN analysis.options_walls.wall_oi               IS 'Total OI of the dominant zone.';
COMMENT ON COLUMN analysis.options_walls.mean_oi              IS 'Unused (legacy 80pct/large_num wall column, always NULL).';
COMMENT ON COLUMN analysis.options_walls.threshold             IS 'Threshold parameter for the wall: 0.06 (6% minimum chain OI mass share) for zone.';
COMMENT ON COLUMN analysis.options_walls.wall_low              IS 'zone only: low strike of the dominant OI zone, RAW strike units (same scale as stats.options_strike / underlying_close).';
COMMENT ON COLUMN analysis.options_walls.wall_high             IS 'zone only: high strike of the dominant OI zone, RAW strike units.';
COMMENT ON COLUMN analysis.options_walls.wall_center           IS 'zone only: OI-weighted mean strike of the zone (center of mass), RAW strike units.';
COMMENT ON COLUMN analysis.options_walls.mass_share            IS 'zone only: zone OI / total chain OI (call+put across all strikes), in [0,1]. Empirically >=0.06 (big wall) is the level at which a call wall adds ~20pp hold-rate over a small wall at equal distance.';
COMMENT ON COLUMN analysis.options_walls.gap_pct               IS 'zone only: distance of the zone center from spot, in % of spot, signed away-from-spot (CALL: center-spot; PUT: spot-center; negative = breached). NULL when the underlying close is missing.';
COMMENT ON COLUMN analysis.options_walls.days_persisted        IS 'zone only: consecutive trading days the zone has existed, matched day-over-day within (underlying, expiry, side) by >=50% strike-range overlap. 1 = fresh zone (fresh walls hold measurably worse).';
COMMENT ON COLUMN analysis.options_walls.state                 IS 'zone only: lifecycle state. ACTIVE = intact barrier; ERODED = intact but mass fell below 70% of the previous day''s zone mass; BREACHED = spot closed beyond the zone (CALL: spot > wall_high; PUT: spot < wall_low). Breaches historically continue (~2/3), flipping the zone from barrier to momentum trigger.';
COMMENT ON COLUMN analysis.options_walls.strength_score        IS 'zone only: strength = mass_share * exp(-max(gap_pct,0)/8) * (1 + 0.25*min(days_persisted,20)/20), in [0,1]. The exponential decay matches the measured hold-rate curve (58% hold at ~1% gap -> 99% at >8%).';

-- ----------------------------------------------------------------------------
--  Migration for pre-existing options_walls tables (idempotent):
--  drop the legacy 80pct/large_num wall types — wall_type must be 'zone'.
--  Safe to re-run.
-- ----------------------------------------------------------------------------
DELETE FROM analysis.options_walls WHERE wall_type <> 'zone';
-- Drop EVERY legacy wall_type check (the original inline CHECK was
-- auto-renamed with a numeric suffix on name collision) on the parent
-- + hash partitions, then enforce zone-only.
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN
        SELECT con.conrelid::regclass AS tbl, con.conname
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'analysis'
          AND (c.relname = 'options_walls' OR c.relname LIKE 'options_walls_p%')
          AND con.contype = 'c'
          AND con.conname LIKE 'options_walls_wall_type_check%'
    LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT IF EXISTS %I', r.tbl, r.conname);
    END LOOP;
END $$;
ALTER TABLE analysis.options_walls
    ADD CONSTRAINT options_walls_wall_type_check
        CHECK (wall_type = 'zone');

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_skewness_stats', 'options_skewness_stats', NULL, NOW(),
     'Per-(date, option_type, underlying_code, expiry_date, skew_type) store of precomputed rolling skewness statistics for option expiry groups, for multiple skew data sources separated by skew_type: oi_moneyness = OI-weighted mean moneyness (strike_price / underlying_close) — a positioning metric; iv_smile = OI-weighted 3rd standardized moment of implied vol across strikes (from stats.options_greeks) — a pricing metric; greek_delta = delta-weighted put/call OI ratio (whole chain, neutral 0.5); greek_gamma = normalized GEX-style call-minus-put gamma balance (whole chain, neutral 0); greek_vega = OTM-wing vega balance (0<|delta|<0.5 wings, neutral 0 — the open-interest mirror of the 25d risk reversal). The greek_* metrics are PAIR-level CALL-vs-PUT contrasts (CALL/PUT rows of a pair hold the same value); theta/rho are not computed (no industry-standard positioning skew). Rolling windows (5/20/60 days) compute MA, STD, gap-from-neutral, linear regression slope of gap, and whole-period cumulative correlation with spot. For open (non-matured) expiry groups, expiry_date is set to the mean of all expiry dates per (option_type, underlying_code). FK -> analysis.options_expiry_identity. Built by analyze.options; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_oi_stats', 'options_oi_stats', NULL, NOW(),
     'Per-(date, option_type, underlying_code, expiry_date) store of precomputed options OI-related statistics for expiry groups. Currently stores corr_put_call_ratio_vs_spot — rolling correlation between put/call OI ratio and underlying spot price. FK -> analysis.options_expiry_identity. Built by analyze.options; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_iv_skew_stats', 'options_iv_skew_stats', NULL, NOW(),
     'Per-(date, option_type, underlying_code, expiry_date) store of implied-volatility skew statistics for option expiry groups, derived from implied_vol in stats.options_greeks (calibrated from option premiums via Black-76). All IV/skew values are in vol points (percent). Daily metrics: atm_iv, iv_call25/iv_put25 (IV of OTM contract nearest |delta|=0.25), risk_reversal_25d = iv_call25 - iv_put25, put_skew_25d, call_skew_25d, smile_skewness (OI-weighted 3rd moment of IV). Rolling suite (5/20/60 days) on risk_reversal_25d: MA, STD, full-history slopes, expanding correlation with spot MA. For open (non-matured) expiry groups, expiry_date is collapsed to the mean of all expiry dates per (option_type, underlying_code). FK -> analysis.options_expiry_identity. Built by analyze.options; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_walls', 'options_walls', NULL, NOW(),
     'Per-(date, option_type, underlying_code, expiry_date, wall_type) store of precomputed options wall levels. Single wall type: zone (strength-scored OI wall ZONE with lifecycle: strikes with OI >=2% of chain OI are clustered into adjacent-strike zones (<=2 strike intervals apart); the dominant zone per side carries wall_low/wall_high/wall_center (raw strike units), mass_share (zone OI / chain OI, eligible >=0.06), gap_pct (signed center-vs-spot distance), a lifecycle state machine (ACTIVE / ERODED = mass fell below 70% of previous day / BREACHED = spot beyond the zone) with day-over-day >=50% strike-range overlap persistence tracking (days_persisted), and strength_score = mass_share * exp(-max(gap_pct,0)/8) * (1 + 0.25*min(days_persisted,20)/20). FK -> analysis.options_expiry_identity. Built by analyze.options.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

