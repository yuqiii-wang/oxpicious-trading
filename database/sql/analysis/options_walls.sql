-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Open-interest wall zones per expiry group — the strike zones whose standing volume caps rallies (call walls) or floors dips (put walls).
-- tags: options, 期权, wall, 墙, open interest, 持仓量, support resistance, 支撑压力


CREATE TABLE IF NOT EXISTS analysis.options_walls (
    date                      DATE          NOT NULL,
    option_type               SMALLINT      NOT NULL,  -- label code (static.label_dim 'option_type'): 0=CALL 1=PUT
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

COMMENT ON TABLE  analysis.options_walls                       IS 'Per-(underlying_code, date, option_type, expiry_date, wall_type) store of precomputed options wall levels. Single wall type: zone (strength-scored OI wall ZONE with lifecycle — see column comments). For CALL walls the wall acts as resistance/cap, for PUT walls as support/floor. FK -> analysis.options_expiry_identity. Built by analysis.options.';
COMMENT ON COLUMN analysis.options_walls.date                  IS 'Trading date.';
COMMENT ON COLUMN analysis.options_walls.option_type           IS 'Option type as its static.label_dim (''option_type'' domain) SMALLINT code: 0 = CALL, 1 = PUT.';
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
     'Per-(date, option_type, underlying_code, expiry_date, skew_type) store of precomputed rolling skewness statistics for option expiry groups, for multiple skew data sources separated by skew_type: oi_moneyness = OI-weighted mean moneyness (strike_price / underlying_close) — a positioning metric; iv_smile = OI-weighted 3rd standardized moment of implied vol across strikes (from stats.options_greeks) — a pricing metric; greek_delta = delta-weighted put/call OI ratio (whole chain, neutral 0.5); greek_gamma = normalized GEX-style call-minus-put gamma balance (whole chain, neutral 0); greek_vega = OTM-wing vega balance (0<|delta|<0.5 wings, neutral 0 — the open-interest mirror of the 25d risk reversal). The greek_* metrics are PAIR-level CALL-vs-PUT contrasts (CALL/PUT rows of a pair hold the same value); theta/rho are not computed (no industry-standard positioning skew). Rolling windows (5/20/60 days) compute MA, STD, gap-from-neutral, linear regression slope of gap. For open (non-matured) expiry groups, expiry_date is set to the mean of all expiry dates per (option_type, underlying_code). FK -> analysis.options_expiry_identity. Built by analysis.options; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_oi_stats', 'options_oi_stats', NULL, NOW(),
     'Dedicated per-expiry options OI statistics, keyed by REAL expiry date (not the collapsed open-group convention; no FK to options_expiry_identity for that reason). Per (date, option_type, underlying_code, expiry_date): oi_total = calls+puts open interest of the expiry group (contracts), oi_delta_5d / oi_delta_20d = change vs 5/20 trading sessions earlier (NULL when the group had no row at the offset session), oi_max_20d = max oi_total over the trailing 20 sessions incl. the date. Serves the per-expiry OI tooltip stats (OI / Δ5d / Δ20d / 20d max) of the OI-weighted moneyness skew panel. Built by analysis.options; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_iv_skew_stats', 'options_iv_skew_stats', NULL, NOW(),
     'Per-(date, option_type, underlying_code, expiry_date) store of implied-volatility skew statistics for option expiry groups, derived from implied_vol in stats.options_greeks (calibrated from option premiums via Black-76). All IV/skew values are in vol points (percent). Daily metrics: atm_iv, iv_call25/iv_put25 (IV of OTM contract nearest |delta|=0.25), risk_reversal_25d = iv_call25 - iv_put25, put_skew_25d, call_skew_25d, smile_skewness (OI-weighted 3rd moment of IV). Rolling suite (5/20/60 days) on risk_reversal_25d: MA, STD, full-history slopes, expanding correlation with spot MA. For open (non-matured) expiry groups, expiry_date is collapsed to the mean of all expiry dates per (option_type, underlying_code). FK -> analysis.options_expiry_identity. Built by analysis.options; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_walls', 'options_walls', NULL, NOW(),
     'Per-(date, option_type, underlying_code, expiry_date, wall_type) store of precomputed options wall levels. Single wall type: zone (strength-scored OI wall ZONE with lifecycle: strikes with OI >=2% of chain OI are clustered into adjacent-strike zones (<=2 strike intervals apart); the dominant zone per side carries wall_low/wall_high/wall_center (raw strike units), mass_share (zone OI / chain OI, eligible >=0.06), gap_pct (signed center-vs-spot distance), a lifecycle state machine (ACTIVE / ERODED = mass fell below 70% of previous day / BREACHED = spot beyond the zone) with day-over-day >=50% strike-range overlap persistence tracking (days_persisted), and strength_score = mass_share * exp(-max(gap_pct,0)/8) * (1 + 0.25*min(days_persisted,20)/20). FK -> analysis.options_expiry_identity. Built by analysis.options.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
