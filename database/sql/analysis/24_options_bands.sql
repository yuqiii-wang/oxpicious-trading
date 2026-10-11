-- ============================================================================
--  Options Bands v2 — OI-MASS CORRIDOR bands per (underlying_code, date):
--  the options-bounded region and how spot moves within it.
--
--  Table: analysis.options_bands
--    PK: (underlying_code, date)
--
--  v2 REDEFINITION (2026-10-04, temp_scripts/study_skew_position_bands.py,
--  3 CFFEX index underlyings 2020-2026, ~3,500 underlying-days). The v1
--  nearest-to-spot wall-zone bands carried no signal on days like
--  2026-03-23 (whole OI mass overhead, spot at the window low, +4% in the
--  next two weeks). The validated semantics:
--    * The bounded region = the OI mass corridor: strikes enclosing the
--      central half of pooled chain OI (all expiries, CDF P25..P75) —
--      the quartile edges coincide with the validated lean thresholds.
--    * How spot moves in it: when spot presses/exits the corridor's
--      LOWER edge it snaps back UP — close <= P10: 90% up over the next
--      5 sessions, mean +4.5% (n=10); spot_pct <= 10% bucket: +7.1%
--      (n=5); 10-25% bucket: mean 10-session +1.4% (n=336) — and the
--      mirror above the UPPER edge snaps DOWN (close >= P90: 89% down,
--      mean -3.3%, n=9). Asymmetric strength: the low side is the
--      reliable one (up-drift market).
--    * The moneyness-skew LEVEL flags (all expiries' E[M] > 1 / < 1)
--      carry NO standalone edge (708/3,500 days = 20% frequency,
--      up-rate 0.484 vs 0.507 base; corr(min_em, fwd5) = -0.006) — kept
--      as CONTEXT columns only. They matter combined with position:
--      all_above & spot_pct <= 25% has the strongest 10-session drift
--      (+1.57%, n=289; e.g. 2026-03-23 000300: spot_pct 0.245).
-- ============================================================================
DROP TABLE IF EXISTS analysis.options_bands;

CREATE TABLE IF NOT EXISTS analysis.options_bands (
    date                      DATE          NOT NULL,
    underlying_code           TEXT          NOT NULL,

    underlying_close          NUMERIC(14,4),   -- settlement underlying close (raw units)
    corridor_low              NUMERIC(14,4),   -- OI-CDF P25 strike (raw units): bounded-region floor
    corridor_high             NUMERIC(14,4),   -- OI-CDF P75 strike (raw units): bounded-region cap
    oi_total                  NUMERIC(16,2),   -- pooled chain OI (calls+puts, all expiries, contracts)
    spot_pct                  NUMERIC(6,4),    -- OI share at strikes <= spot, [0,1] (position in the mass)

    gap_low_pct               NUMERIC(10,4),   -- (close - corridor_low)/close*100; negative = below the corridor
    gap_high_pct              NUMERIC(10,4),   -- (corridor_high - close)/close*100; negative = above the corridor
    pos_state                 TEXT
        CHECK (pos_state IN ('BELOW','INSIDE','ABOVE')),
    lean                      TEXT
        CHECK (lean IN ('UP','DOWN','NEUTRAL')),
    event                     TEXT
        CHECK (event IN ('NONE','EXIT_LOW','EXIT_HIGH','REENTER')),

    -- Exit-quality re-study (2026-10-05,
    -- temp_scripts/study_corridor_events_v2.py, 7 underlyings ~4,700
    -- underlying-days): the stored event is a one-day binary cross of
    -- the PRIOR session's corridor edges; ~50% of exits breach by less
    -- than 0.5x the day's own fluctuation (edge re-centred onto spot —
    -- sub-fluctuation noise) and CONTINUE in the breach direction, while
    -- deep breaches (>= 1.5x sigma20) are the capitulation flush that
    -- snaps back (pooled 80% up over 5 sessions, mean +2.0%; consistent
    -- per underlying: 000300 87.5%, 000852/000016 66.7%). Wall context:
    -- a low-side exit that also closes below the chain-dominant PUT
    -- wall's low while that wall was ACTIVE (the defended insurance
    -- floor giving way) is the strongest cell (80% up, mean 5-session
    -- +3.12%).
    sigma20                   NUMERIC(10,4),   -- trailing 20-session std of daily % returns: the spot fluctuation unit
    dev_edge_pct              NUMERIC(10,4),   -- exit depth beyond the PRIOR session's corridor edge, % of spot (positive = outside)
    dev_z                     NUMERIC(8,3),    -- dev_edge_pct / sigma20: breach depth in fluctuation units
    exit_class                TEXT             -- FLUSH (dev_z >= 1.5) / BREAK (0.5..1.5) / NOISE (< 0.5); NULL on non-exit days or unknown sigma
        CHECK (exit_class IN ('FLUSH','BREAK','NOISE')),
    wall_side_broken          INTEGER,         -- 1 = close beyond the PRIOR session's chain-dominant wall edge on the exit side (insurance floor/cap gave way); 0 = wall held; NULL otherwise
    wall_state_prior          TEXT             -- prior-session lifecycle state of the chain-dominant wall on the exit side (NULL = no wall that day)
        CHECK (wall_state_prior IN ('ACTIVE','ERODED','BREACHED')),

    -- Fresh strike-grid expansion (trailing BANDS_STRIKE_WIN_SESSIONS=40
    -- sessions): the exchange lists new strikes as the underlying moves —
    -- a burst of new higher/lower strikes marks genuine range extension
    -- (e.g. the 2025-08-22..26 rally: 000300 grid ceiling 4700 -> 5000,
    -- 6 new higher strikes -> the 2025-08-28 EXIT_HIGH breach into fresh
    -- price territory is AMPLIFIED, not routine).
    new_hi_40d                INTEGER,         -- distinct strikes present ABOVE the chain's max strike of 40 sessions earlier (NULL when <40 prior sessions)
    new_lo_40d                INTEGER,         -- mirror BELOW the min strike of 40 sessions earlier
    edge_sig                  TEXT             -- edge-engagement amplified by same-side grid expansion (see COMMENT)
        CHECK (edge_sig IN ('NONE','UP','DOWN','BOTH')),

    n_expiries                INTEGER,         -- expiry groups with OI that date
    skew_all_above            INTEGER,         -- 1 = every expiry group E[M] > 1 (mass overhead) — context, no standalone edge
    skew_all_below            INTEGER,         -- 1 = every expiry group E[M] < 1 (mass underfoot) — context
    min_em                    NUMERIC(10,6),   -- tightest expiry's E[M] = sum(OI*K)/sum(OI*S), all contracts
    mean_em                   NUMERIC(10,6),

    em5_pct                   NUMERIC(8,4),    -- 5-session IV expected move, % of spot (NULL below plausibility floor)
    ret5_pct                  NUMERIC(10,4),   -- trailing 5-session return, %
    exhausted_5d              INTEGER,         -- 1 = |ret5_pct| >= em5_pct

    CONSTRAINT pk_options_bands
        PRIMARY KEY (underlying_code, date)
) PARTITION BY HASH (underlying_code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'options_bands', 8);

COMMENT ON TABLE  analysis.options_bands IS 'Options-implied OI-MASS CORRIDOR bands per (underlying_code, date): the bounded region = strikes enclosing the central half of pooled chain OI (all expiries; corridor_low/corridor_high = OI-CDF P25/P75), with spot_pct (OI share at strikes <= spot), signed edge gaps, pos_state (BELOW/INSIDE/ABOVE), a lean (UP when at/below the corridor bottom or spot_pct <= 0.25 — validated snap-back: close <= P10 = 90% up, mean +4.5%; DOWN when at/above the top or spot_pct >= 0.90 — 89% down, mean -3.3%; NEUTRAL between), and an EVENT vs the PRIOR session''s corridor (EXIT_LOW / EXIT_HIGH / REENTER — fresh crosses, immune to same-day re-centring). Exit quality (2026-10-05 re-study, ~4,700 underlying-days): sigma20/dev_edge_pct/dev_z/exit_class grade every exit by breach depth in fluctuation units — NOISE (< 0.5x sigma20, ~half of all exits) CONTINUES in the breach direction, FLUSH (>= 1.5) is the capitulation snap-back (80% up, mean 5-session +2.0%), BREAK between; wall_side_broken/wall_state_prior carry the chain-dominant wall context (an ACTIVE put wall''s floor giving way on an EXIT_LOW = the strongest cell: 80% up, mean +3.12%). Moneyness-skew context columns (skew_all_above/below, min_em, mean_em) carry NO standalone edge (20% frequency, up-rate below base) and are context only; the strong combined state is all_above & spot_pct <= 25% (mean 10-session +1.57%, n=289). em5_pct = 5-session chain-ATM IV expected move (NULL below the 10-vol-point plausibility floor — ETF greeks miscalibration); exhausted_5d = |ret5| >= em5. No FK (date-granular). Built by analysis.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_bands.corridor_low       IS 'OI-CDF P25 strike in raw units (same scale as underlying_close / stats.options_strike): the floor of the options-bounded region — the validated UP-lean boundary (spot below it = spot_pct <= 0.25, mean 10-session +1.4%).';
COMMENT ON COLUMN analysis.options_bands.corridor_high      IS 'OI-CDF P75 strike (raw units): the cap of the options-bounded region (the strong DOWN zone only starts at the P90 decile).';
COMMENT ON COLUMN analysis.options_bands.spot_pct           IS 'Share of pooled OI at strikes <= spot, [0,1]: 0.25 = spot in the bottom quartile of the OI mass (snap-back-up lean zone); 0.90 = top decile.';
COMMENT ON COLUMN analysis.options_bands.pos_state          IS 'BELOW = close < corridor_low; INSIDE = within the corridor; ABOVE = close > corridor_high.';
COMMENT ON COLUMN analysis.options_bands.lean               IS 'Validated direction lean inside the bounded region: UP when BELOW or spot_pct <= 0.25 (10-25% bucket mean 10-session +1.4%; edge touches 90% up); DOWN when ABOVE or spot_pct >= 0.90 (89% down at the P90 edge); NEUTRAL otherwise. The low side is the statistically reliable one.';
COMMENT ON COLUMN analysis.options_bands.event              IS 'Fresh corridor cross vs the PRIOR session''s corridor edges (tradeable axis): EXIT_LOW = prior close >= prior corridor_low, now below; EXIT_HIGH = prior close <= prior corridor_high, now above; REENTER = prior close outside, now inside; NONE otherwise/first day.';
COMMENT ON COLUMN analysis.options_bands.sigma20            IS 'Trailing 20-session std of daily % returns (the spot fluctuation unit): the normalizer that separates sub-fluctuation edge noise from genuine breaches. NULL on the first 20 sessions of a history.';
COMMENT ON COLUMN analysis.options_bands.dev_edge_pct       IS 'Exit depth beyond the PRIOR session''s corridor edge, % of spot (positive = outside): EXIT_LOW = (prior_low - close)/close*100, EXIT_HIGH = (close - prior_high)/close*100; NULL on non-exit days.';
COMMENT ON COLUMN analysis.options_bands.dev_z              IS 'dev_edge_pct / sigma20: breach depth in fluctuation units. Re-study (2026-10-05, temp_scripts/study_corridor_events_v2.py, ~4,700 underlying-days): dev_z < 0.5 exits CONTINUE in the breach direction (low side 29% up, mean 5-session -0.71% — half of all exits are this noise), dev_z >= 1.5 flushes snap back (80% up, mean +2.0%; per underlying 000300 87.5% / 000852 / 000016 66.7%).';
COMMENT ON COLUMN analysis.options_bands.exit_class         IS 'Exit-quality class by dev_z: FLUSH (>= 1.5, capitulation-grade breach — the validated snap-back cell), BREAK (0.5..1.5, genuine but unresolved), NOISE (< 0.5, sub-fluctuation cross — continuation-leaning, not a level signal). NULL on non-exit days or when sigma20 is unknown.';
COMMENT ON COLUMN analysis.options_bands.wall_side_broken   IS '1 = the exit also closed beyond the PRIOR session''s chain-dominant wall edge on the exit side (the defended insurance floor/cap gave way — strongest observed cell: EXIT_LOW & floor broken & wall ACTIVE = 80% up, mean 5-session +3.12%), 0 = the wall held, NULL on non-exit days or when no eligible wall existed.';
COMMENT ON COLUMN analysis.options_bands.wall_state_prior   IS 'Prior-session lifecycle state (ACTIVE/ERODED/BREACHED) of the chain-dominant wall on the exit side: an ACTIVE wall breaking carries the insurance-capitulation reading; NULL when no eligible wall existed that prior session.';
COMMENT ON COLUMN analysis.options_bands.skew_all_above     IS 'CONTEXT ONLY (no standalone edge): 1 when every expiry group''s E[M] = sum(OI*K)/sum(OI*S) > 1 — the whole OI mass overhead. Strong only combined with position: all_above & spot_pct <= 25% = mean 10-session +1.57% (n=289, 2020-2026).';
COMMENT ON COLUMN analysis.options_bands.skew_all_below    IS 'CONTEXT ONLY: 1 when every expiry group''s E[M] < 1 — whole mass underfoot (no validated edge alone).';
COMMENT ON COLUMN analysis.options_bands.new_hi_40d       IS 'Distinct strikes present on the date ABOVE the chain''s max strike of 40 sessions earlier: fresh UPSIDE strike-grid expansion (exchanges list new higher strikes as the underlying rallies toward the old ceiling). NULL when <40 prior sessions in history. Grids step by 2 strikes at a time, so >= 2 = one genuine expansion step.';
COMMENT ON COLUMN analysis.options_bands.new_lo_40d       IS 'Mirror of new_hi_40d BELOW the chain''s min strike of 40 sessions earlier (fresh DOWNSIDE grid expansion).';
COMMENT ON COLUMN analysis.options_bands.edge_sig         IS 'Edge-significance amplifier: a breached/approached corridor edge (upper: lean DOWN or EXIT_HIGH; lower: lean UP or EXIT_LOW) whose SAME side just saw fresh strike-grid expansion (>= 2 new strikes in the trailing 40 sessions) — the breach is into freshly-listed price territory, not routine corridor noise. UP = upper edge amplified (new higher strikes), DOWN = lower edge amplified (new lower strikes), BOTH = both sides, NONE = not amplified (NULL when the 40-session window is incomplete). e.g. 000300 2025-08-28: EXIT_HIGH with new_hi_40d=6 -> UP.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_bands', 'options_bands', NULL, NOW(),
     'Options-implied OI-MASS CORRIDOR bands per (underlying_code, date): the bounded region = strikes enclosing the central 80% of pooled chain OI (corridor_low/high = OI-CDF P25/P75), with spot_pct (OI share below spot), signed edge gaps, pos_state (BELOW/INSIDE/ABOVE), a validated direction lean (UP at/below the corridor bottom — close <= P10 = 90% up next 5 sessions, mean +4.5%; DOWN at/above the top — 89% down, mean -3.3%), and prior-session corridor events (EXIT_LOW/EXIT_HIGH/REENTER). Exit quality (2026-10-05 re-study): sigma20/dev_edge_pct/dev_z/exit_class grade every exit by breach depth in fluctuation units (NOISE < 0.5x sigma20 — ~half of all exits, continuation-leaning; BREAK 0.5..1.5; FLUSH >= 1.5 — the 80%-up snap-back cell), and wall_side_broken/wall_state_prior carry the chain-dominant wall context (an ACTIVE put wall''s floor giving way on an EXIT_LOW = the strongest cell, 80% up, mean +3.12%). Edge significance: new_hi_40d/new_lo_40d count strikes newly present beyond the chain''s max/min strike of 40 sessions earlier (fresh exchange listing expansion), and edge_sig marks a breached/approached edge whose own side just expanded by >= 2 new strikes (UP/DOWN/BOTH; e.g. 000300 2025-08-28 EXIT_HIGH with new_hi_40d=6 -> UP — a breach into freshly-listed price territory). Moneyness-skew flags (skew_all_above/below, min_em, mean_em) are context-only (no standalone edge; strongest combined state: all_above & spot_pct <= 25%, mean 10-session +1.57%). em5_pct = 5-session chain-ATM IV expected move; exhausted_5d = |ret5| >= em5. PK (underlying_code, date); no FK. Built by analysis.options; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
