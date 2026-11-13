-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Rolling positioning and pricing skew statistics per expiry group — OI-weighted moneyness, the IV smile and the delta/gamma/vega put-call balances.
-- tags: options, 期权, skew, 偏度, moneyness, 价度, put call ratio, 认沽认购比


CREATE TABLE IF NOT EXISTS analysis.options_skewness_stats (
    date                      DATE          NOT NULL,
    option_type               SMALLINT      NOT NULL,  -- label code (static.label_dim 'option_type'): 0=CALL 1=PUT
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

COMMENT ON TABLE  analysis.options_skewness_stats              IS 'Per-(underlying_code, date, option_type, expiry_date, skew_type) store of precomputed rolling skewness statistics for option expiry groups, for multiple skew data sources separated by skew_type: oi_moneyness = OI-weighted mean moneyness (strike_price / underlying_close) — a positioning metric; iv_smile = OI-weighted 3rd standardized moment of implied vol across strikes (from stats.options_greeks) — a pricing metric; greek_delta = delta-weighted put/call OI ratio dpcr (whole chain, neutral 0.5); greek_gamma = normalized GEX-style call-minus-put gamma balance (whole chain, neutral 0); greek_vega = OTM-wing vega balance (0<|delta|<0.5 wings, neutral 0 — the open-interest mirror of the 25d risk reversal). The greek_* metrics are PAIR-level CALL-vs-PUT contrasts (CALL and PUT rows of a pair hold the SAME value), weighted by open_interest with zero OI = zero vote; theta/rho have no industry-standard positioning skew and are not computed. Rolling windows (5/20/60 days) compute MA, STD, gap-from-neutral (skewness_MA − neutral; neutral = 1 / 0.5 / 0 by type) and slope of gap. FK -> analysis.options_expiry_identity. Built by analysis.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_skewness_stats.date                    IS 'Trading date.';
COMMENT ON COLUMN analysis.options_skewness_stats.option_type            IS 'Option type as its static.label_dim (''option_type'' domain) SMALLINT code: 0 = CALL, 1 = PUT. For pair-level metrics (greek_*, and the OI/IV-skew tables) the CALL and PUT rows of the same group hold the SAME value.';
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
