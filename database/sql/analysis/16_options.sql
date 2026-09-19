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
    -- pre-expiry contrarian metrics on the gap (skewness - neutral):
    cross_count_20d             INTEGER           NOT NULL DEFAULT 0, -- neutral crossings in the trailing 20 sessions
    days_since_last_cross       INTEGER           NOT NULL DEFAULT 0, -- sessions since the gap last crossed neutral (0 = crossed today)
    gap_side_share_20d          NUMERIC(6,4),                         -- share of the trailing 20 sessions at/above neutral, in [0,1]

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

CREATE INDEX IF NOT EXISTS idx_options_skewness_stats_expiry
    ON analysis.options_skewness_stats (underlying_code, expiry_date, date);

COMMENT ON TABLE  analysis.options_skewness_stats              IS 'Per-(underlying_code, date, option_type, expiry_date, skew_type) store of precomputed rolling skewness statistics for option expiry groups, for multiple skew data sources separated by skew_type: oi_moneyness = OI-weighted mean moneyness (strike_price / underlying_close) — a positioning metric; iv_smile = OI-weighted 3rd standardized moment of implied vol across strikes (from stats.options_greeks) — a pricing metric; greek_delta = delta-weighted put/call OI ratio dpcr (whole chain, neutral 0.5); greek_gamma = normalized GEX-style call-minus-put gamma balance (whole chain, neutral 0); greek_vega = OTM-wing vega balance (0<|delta|<0.5 wings, neutral 0 — the open-interest mirror of the 25d risk reversal). The greek_* metrics are PAIR-level CALL-vs-PUT contrasts (CALL and PUT rows of a pair hold the SAME value), weighted by open_interest with zero OI = zero vote; theta/rho have no industry-standard positioning skew and are not computed. Rolling windows (5/20/60 days) compute MA, STD, gap-from-neutral (skewness_MA − neutral; neutral = 1 / 0.5 / 0 by type) and slope of gap. FK -> analysis.options_expiry_identity. Built by analyze.options; all INSERTs in Python per project rule.';
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
COMMENT ON COLUMN analysis.options_skewness_stats.cross_count_20d          IS 'Pre-expiry contrarian metric: number of neutral crossings in the TRAILING 20 SESSIONS of the gap (skewness − the type''s neutral anchor). A crossing is a day where the gap''s sign bucket (>= 0 vs < 0) differs from the previous day''s; NaN gaps neither cross nor reset. Comparable across expiry groups and recency-weighted (unlike a cumulative counter). High counts = positioning contested around neutral into expiry (choppy, mean-reverting); low counts with a large gap = established one-sided positioning.';
COMMENT ON COLUMN analysis.options_skewness_stats.days_since_last_cross    IS 'Pre-expiry contrarian metric: trading days since the gap (skewness − neutral) last crossed the neutral anchor, 0 = crossed today. Freshness of the last flip: a small value marks a just-flipped regime; a large value (or the group''s age in days while never crossed) marks an established, unchallenged positioning into expiry.';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_side_share_20d       IS 'Pre-expiry contrarian metric: fraction of the trailing 20 sessions with the gap (skewness − neutral) at/above neutral, in [0,1]; NaN-gap days are excluded from both numerator and denominator. Measures one-sided crowding: values near 0 or 1 = persistent positioning on one side of neutral into expiry (crowded trade, contrarian fade); values near 0.5 = contested, choppy positioning.';

-- Legacy-install data fixes (no-ops on fresh installs): backfill rows
-- whose skew_type predates the column default, and purge the legacy
-- greek_* rows (old per-side ATM-normalized centroid semantics, incl. the
-- removed greek_theta/greek_rho) so the incremental pipeline recomputes
-- them with the new PAIR-level metrics.
UPDATE analysis.options_skewness_stats
    SET skew_type = 'oi_moneyness' WHERE skew_type IS NULL;
DELETE FROM analysis.options_skewness_stats
    WHERE skew_type LIKE 'greek_%';

-- Retired skewness-vs-spot whole-period correlation columns (the
-- Skewness–Spot Whole-Period Correlation chart was removed; the
-- rr25-vs-spot correlations of options_iv_skew_stats are unaffected).
ALTER TABLE analysis.options_skewness_stats DROP COLUMN IF EXISTS corr_skewness_ma5_vs_spot_ma5;
ALTER TABLE analysis.options_skewness_stats DROP COLUMN IF EXISTS corr_skewness_ma20_vs_spot_ma20;
ALTER TABLE analysis.options_skewness_stats DROP COLUMN IF EXISTS corr_skewness_ma60_vs_spot_ma60;

-- Pre-expiry contrarian metrics on the gap (skewness − neutral): the
-- recency-aware trio, comparable across expiry groups (the legacy
-- cumulative counter was not carried over). Columns are 0/NULL until
-- rebuilt: run `python -m analyze.options --force` (incremental mode only
-- fills MISSING groups, so it would not refresh existing rows).

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
    iv_call10                   NUMERIC(10,2),   -- IV (vol pts %) of OTM CALL nearest |delta| = 0.10
    iv_put10                    NUMERIC(10,2),   -- IV (vol pts %) of OTM PUT nearest |delta| = 0.10
    risk_reversal_10d           NUMERIC(10,2),   -- iv_call10 - iv_put10 (deeper wing; NULL when no near-0.10 contract)
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

CREATE INDEX IF NOT EXISTS idx_options_iv_skew_stats_expiry
    ON analysis.options_iv_skew_stats (underlying_code, expiry_date, date);

-- Migrate: 10-delta risk reversal added 2026-09-19 (the CREATE TABLE above
-- includes the columns for fresh installs; ADD COLUMN IF NOT EXISTS
-- retro-fits an already-existing table without dropping data). ADD COLUMN
-- propagates to the hash partitions automatically.
ALTER TABLE analysis.options_iv_skew_stats ADD COLUMN IF NOT EXISTS iv_call10         NUMERIC(10,2);
ALTER TABLE analysis.options_iv_skew_stats ADD COLUMN IF NOT EXISTS iv_put10          NUMERIC(10,2);
ALTER TABLE analysis.options_iv_skew_stats ADD COLUMN IF NOT EXISTS risk_reversal_10d NUMERIC(10,2);

COMMENT ON TABLE  analysis.options_iv_skew_stats              IS 'Per-(underlying_code, date, option_type, expiry_date) store of implied-volatility skew statistics for option expiry groups, derived from implied_vol in stats.options_greeks (calibrated from option premiums via Black-76). All IV/skew values are in vol points (percent). Unlike options_skewness_stats (OI-weighted mean moneyness — a positioning metric), this is a pricing metric. FK -> analysis.options_expiry_identity. Built by analyze.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.atm_iv       IS 'IV (vol points, %) of the contract with moneyness (strike/spot) closest to 1.0 in the expiry group.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.iv_call25    IS 'IV (vol points, %) of the OTM CALL contract with delta nearest 0.25.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.iv_put25     IS 'IV (vol points, %) of the OTM PUT contract with delta nearest -0.25.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.risk_reversal_25d IS '25-delta risk reversal: iv_call25 - iv_put25 (vol points). Negative = OTM puts richer than OTM calls = downside hedging demand.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.put_skew_25d  IS 'iv_put25 - atm_iv (vol points); premium paid for downside protection.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.call_skew_25d IS 'iv_call25 - atm_iv (vol points); upside speculation premium.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.iv_call10    IS 'IV (vol points, %) of the OTM CALL contract with delta nearest 0.10 (deeper wing; sparser strike grids may have no such contract -> NULL).';
COMMENT ON COLUMN analysis.options_iv_skew_stats.iv_put10     IS 'IV (vol points, %) of the OTM PUT contract with delta nearest -0.10 (deeper wing; sparser strike grids may have no such contract -> NULL).';
COMMENT ON COLUMN analysis.options_iv_skew_stats.risk_reversal_10d IS '10-delta risk reversal: iv_call10 - iv_put10 (vol points). More crash-sensitive / steeper in panics than risk_reversal_25d, but thinner quotes and staler marks on deep wings.';
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
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_skewness_stats', 'options_skewness_stats', NULL, NOW(),
     'Per-(date, option_type, underlying_code, expiry_date, skew_type) store of precomputed rolling skewness statistics for option expiry groups, for multiple skew data sources separated by skew_type: oi_moneyness = OI-weighted mean moneyness (strike_price / underlying_close) — a positioning metric; iv_smile = OI-weighted 3rd standardized moment of implied vol across strikes (from stats.options_greeks) — a pricing metric; greek_delta = delta-weighted put/call OI ratio (whole chain, neutral 0.5); greek_gamma = normalized GEX-style call-minus-put gamma balance (whole chain, neutral 0); greek_vega = OTM-wing vega balance (0<|delta|<0.5 wings, neutral 0 — the open-interest mirror of the 25d risk reversal). The greek_* metrics are PAIR-level CALL-vs-PUT contrasts (CALL/PUT rows of a pair hold the same value); theta/rho are not computed (no industry-standard positioning skew). Rolling windows (5/20/60 days) compute MA, STD, gap-from-neutral, linear regression slope of gap. For open (non-matured) expiry groups, expiry_date is set to the mean of all expiry dates per (option_type, underlying_code). FK -> analysis.options_expiry_identity. Built by analyze.options; all INSERTs in Python per project rule.')
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


-- ----------------------------------------------------------------------------
-- options_vol_index — daily 30-day model-free implied-volatility index per
-- underlying (CBOE VIX methodology adapted to settlement prices; see
-- docs/options_vol_smile_study.md). Date-granular (NOT expiry-granular) —
-- hence no FK to options_expiry_identity.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis.options_vol_index (
    date                      DATE          NOT NULL,
    underlying_code           TEXT          NOT NULL,

    near_expiry_date          DATE,             -- expiry with T <= 30d used in the bracket
    far_expiry_date           DATE,             -- expiry with T > 30d used in the bracket
    dte_near                  INT,              -- calendar days to near expiry (NULL when single-expiry fallback)
    dte_far                   INT,              -- calendar days to far expiry  (NULL when single-expiry fallback)
    var_near                  NUMERIC(12,6),    -- model-free variance of the near expiry (decimal, e.g. 0.0400)
    var_far                   NUMERIC(12,6),    -- model-free variance of the far expiry
    variance_30d              NUMERIC(12,6),    -- time-interpolated 30-day variance (single-expiry value on fallback)
    vol_index_30d             NUMERIC(10,2),    -- 100 * sqrt(variance_30d), in vol points (percent)

    CONSTRAINT pk_options_vol_index
        PRIMARY KEY (underlying_code, date)
) PARTITION BY HASH (underlying_code);

SELECT public.create_hash_partitions('analysis', 'options_vol_index', 8);

COMMENT ON TABLE  analysis.options_vol_index IS 'Daily 30-day model-free implied-volatility index per underlying_code (CBOE VIX methodology adapted to exchange settlement prices): per expiry sigma^2 = (2/T) * sum_i e^(rT) * (dK_i / K_i^2) * Q(K_i) - (1/T) * (F/K0 - 1)^2 over the OTM strip (puts below the forward, calls above, average of both at K0), F = S*exp(rT), r = 0.02; two expiries bracketing 30 days are time-interpolated to a constant-30d variance, vol_index_30d = 100*sqrt(variance). No FK (date-granular). Built by analyze.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_vol_index.near_expiry_date IS 'Expiry group with the largest T <= 30 days used in the 30-day bracket.';
COMMENT ON COLUMN analysis.options_vol_index.far_expiry_date  IS 'Expiry group with the smallest T > 30 days used in the 30-day bracket.';
COMMENT ON COLUMN analysis.options_vol_index.dte_near        IS 'Calendar days from date to near_expiry_date.';
COMMENT ON COLUMN analysis.options_vol_index.dte_far         IS 'Calendar days from date to far_expiry_date.';
COMMENT ON COLUMN analysis.options_vol_index.var_near        IS 'Model-free variance (decimal) of the near expiry.';
COMMENT ON COLUMN analysis.options_vol_index.var_far         IS 'Model-free variance (decimal) of the far expiry.';
COMMENT ON COLUMN analysis.options_vol_index.variance_30d    IS 'Time-interpolated constant-30-day variance; equals the single nearest expiry variance when no bracket exists (early listings).';
COMMENT ON COLUMN analysis.options_vol_index.vol_index_30d   IS '100 * sqrt(variance_30d) — the index level in vol points (percent), directly comparable to VIX.';

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_vol_index', 'options_vol_index', NULL, NOW(),
     'Daily 30-day model-free implied-volatility index per underlying (CBOE VIX methodology adapted to settlement prices, r=0.02, calendar-day T): OTM-strip variance replication per expiry, time-interpolated to a constant 30-day maturity, vol_index_30d = 100*sqrt(variance_30d) in vol points. PK (underlying_code, date), no FK (date-granular). Built by analyze.options; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
