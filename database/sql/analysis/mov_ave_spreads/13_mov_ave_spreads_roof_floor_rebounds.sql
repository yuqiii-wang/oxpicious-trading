-- ============================================================================
--  mov_ave_spreads suite — ROOF/FLOOR REBOUND AUDIT.
--  Formulates the 2026-10 retest study (temp_scripts/
--  study_floor_roof_retest.py) into an auditable mov_ave table: every
--  recorded W-day extreme (episode extreme) of a code, whether it
--  REBOUNDED, and — once rebounded — whether the level HELD or BROKE on
--  retest, with the rebound/bounce/break amplitudes. Shared conventions
--  (sec_type handling, the NO-FK derived-data design): see
--  01_mov_ave_spreads_detail.sql; anchor semantics: see
--  03_mov_ave_spreads_roof_and_floor_lines.sql.
-- ============================================================================

-- Drop any prior version so a re-run rebuilds the table from the CREATE
-- below; also remove the stale analysis_identity row so the registration
-- starts clean. The table is derived data, WHOLESALE-rebuilt per
-- sec_type on every mov_ave_spread run that computes the sec_type
-- (event OUTCOMES depend on data AFTER the event date — retest dates,
-- later breaks — so PK-coverage incremental does not apply; the
-- high_low_pct_streaks precedent).
DROP TABLE IF EXISTS analysis.mov_ave_spreads_roof_floor_rebounds CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_spread_roof_floor_rebounds';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_spreads_roof_floor_rebounds
--    One row per RECORDED EXTREME EVENT of a code: the episode extreme
--    (argmin/argmax close of a cluster of fresh W-day extremes; see the
--    event definition below) for side floor (recorded MIN) or roof
--    (recorded MAX), at scale window ∈ {60, 120, 255, 500, 750, 1275}
--    trading days.
--
--  EVENT DEFINITION (the 2026-10 study parameterization):
--    candidate  : close[t] is the min/max of close[t-window+1, t+10]
--                 (a fresh W-day extreme that holds for K=10 more
--                 trading days), computed on CLOSES (wicks ignored)
--    episode    : candidates within 40 trading days of the cluster's
--                 first candidate collapse into ONE episode; the event
--                 is the episode's extreme-close date and level
--    rebound    : a close reaches level*(1 ± 5%) within 60 trading days
--                 of the event (floor: +5% up; roof: -5% down)
--    retest     : after the rebound completes, the first day whose LOW
--                 (floor) / HIGH (roof) enters the ±1% zone around the
--                 level
--    outcome (static.label_dim 'rf_rebound_outcome'):
--      no_rebound     the extreme never rebounded 5% within 60d (the
--                     rebound-frequency denominator)
--      never_retested rebounded but price never returned to the ±1%
--                     zone by the data end
--      held           first retest bounced: a close 5% back away from
--                     the level BEFORE any close 1% beyond it
--      broken         first retest failed: a close 1% beyond the level
--                     before the 5%-away close
--      unresolved     retested but neither happened by the data end
--                     (right-censored)
--
--  Amplitudes (NUMERIC(10,6) fractions):
--    rebound_pct     close of the rebound-completion day vs level
--    bounce_pct     (held) max favorable close within 60d of the retest
--                   vs level
--    break_depth_pct (broken) max adverse close within 20d of the break
--                   vs level
--
--  Durability extras: clean_break (broken: close still beyond the level
--  20d after the break — the level flipped roles), later_break (held:
--  the level was eventually exceeded on a LATER retest), retest_
--  episodes (distinct zone-touch runs, separated by ≥10 out-of-zone
--  days, before the break or data end).
--
--  NOTE: outcome/amplitude columns are NULL exactly where their branch
--  does not apply (e.g. bounce_pct only for held, break fields only
--  for broken) — outcome alone decides the branch.
--
--  Populated by the internal rebound-audit step of
--  `analysis.mov_ave_spread` (see the roof_floor_rebound step package).
--  Reuses the same source DataFrame as the parent pipeline — no second
--  DB round-trip.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_spreads_roof_floor_rebounds (
    sec_type          SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code              TEXT         NOT NULL,
    side              SMALLINT     NOT NULL,  -- label code (static.label_dim 'side'): top=roof (recorded MAX), bottom=floor (recorded MIN)
    period            INTEGER      NOT NULL,  -- event scale: trailing-window size in trading days {60,120,255,500,750,1275}
    event_date        DATE         NOT NULL,  -- the episode extreme's date (the recorded min/max)

    level             NUMERIC(18,6) NOT NULL, -- the recorded extreme CLOSE on event_date
    outcome           SMALLINT     NOT NULL,  -- label code (static.label_dim 'rf_rebound_outcome')
    rebound_date      DATE,                   -- first close at level*(1±5%) within 60d (NULL for no_rebound)
    retest_date       DATE,                   -- first zone touch after the rebound (NULL for no_rebound / never_retested)
    days_to_retest    INTEGER,                -- trading days event_date -> retest_date
    rebound_pct       NUMERIC(10,6),          -- rebound-completion close vs level (fraction)
    bounce_pct        NUMERIC(10,6),          -- held: max favorable close within 60d of retest vs level
    break_depth_pct   NUMERIC(10,6),          -- broken: max adverse close within 20d of break vs level
    days_to_break     INTEGER,                -- broken: trading days retest_date -> first beyond-level close
    clean_break       BOOLEAN,                -- broken: close still beyond the level 20d after the break
    later_break       BOOLEAN,                -- held: level eventually exceeded on a later retest
    retest_episodes   INTEGER,                -- distinct retest episodes before break / data end

    CONSTRAINT pk_mov_ave_spreads_roof_floor_rebounds
        PRIMARY KEY (code, sec_type, side, period, event_date)
) PARTITION BY HASH (code);

-- Native hash partitions (32) keyed on code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p31
SELECT public.create_hash_partitions('analysis', 'mov_ave_spreads_roof_floor_rebounds', 32);

COMMENT ON TABLE  analysis.mov_ave_spreads_roof_floor_rebounds IS 'Roof/floor REBOUND AUDIT (2026-10 retest study, formulated as a table): one row per recorded extreme event — the episode extreme (argmin/argmax close of a cluster of fresh W-day extremes, candidates = close[t] is the extreme of close[t-window+1, t+10], clusters within 40 trading days, computed on closes) — with its REBOUND and RETEST outcome. side via static.label_dim (''side''): top = roof (recorded MAX), bottom = floor (recorded MIN). period ∈ {60,120,255,500,750,1275} trading days (the trailing-window scale W). rebound = a close at level*(1±5%) within 60 trading days; retest = the first day after the rebound whose low (floor) / high (roof) enters the ±1% zone; outcome via static.label_dim (''rf_rebound_outcome''): no_rebound (rebound-frequency denominator), never_retested, held (a close 5% back away before any close 1% beyond the level), broken (a close 1% beyond first), unresolved (right-censored). Amplitudes as fractions: rebound_pct (rebound-completion close vs level), bounce_pct (held: max favorable close within 60d of retest), break_depth_pct (broken: max adverse close within 20d of the break). Durability: clean_break (broken and still beyond the level 20d later — role flip), later_break (held but eventually exceeded on a later retest), retest_episodes (distinct zone-touch runs separated by >=10 out-of-zone days). WHOLESALE-rebuilt per sec_type on every run that computes the sec_type (outcomes depend on post-event data — the high_low_pct_streaks precedent). Source: same DataFrame as the mov_ave_spread parent pipeline (no second DB round-trip). sec_type ∈ {etf, index, stock}.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.sec_type IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.code IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.side IS 'The event''s side as its static.label_dim (''side'' domain) SMALLINT code: top = roof (a recorded MAXIMUM event), bottom = floor (a recorded MINIMUM event).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.period IS 'Event scale: the trailing-window size W in trading days — one of {60, 120, 255, 500, 750, 1275} (the roof/floor windows). Part of the PK.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.event_date IS 'Date of the episode''s extreme close — the recorded min (floor) / max (roof). Part of the PK.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.level IS 'The recorded extreme CLOSE on event_date (wicks ignored). NOT NULL.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.outcome IS 'The event''s audit outcome as its static.label_dim (''rf_rebound_outcome'') SMALLINT code: 0 = no_rebound (no close at level*(1±5%) within 60 trading days — the rebound-frequency denominator), 1 = never_retested (rebounded but the ±1% zone was never re-entered), 2 = held (first retest resolved with a close 5% back away before any close 1% beyond the level), 3 = broken (a close 1% beyond the level first), 4 = unresolved (retested, neither happened by the data end — right-censored).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.rebound_date IS 'First close at level*(1+5%) (floor) / level*(1-5%) (roof) within 60 trading days of event_date. NULL exactly for outcome = no_rebound.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.retest_date IS 'First trading day AFTER the rebound completed whose intraday LOW (floor) / HIGH (roof) entered the ±1% zone around the level. NULL for no_rebound / never_retested.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.days_to_retest IS 'Trading days between event_date and retest_date. NULL for no_rebound / never_retested.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.rebound_pct IS 'Rebound amplitude: (rebound-completion close / level) - 1 (signed: + for floor, - for roof). NULL exactly for no_rebound.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.bounce_pct IS 'Held-branch amplitude: max favorable close within 60 trading days of retest_date vs level, as a positive fraction (floor: (max close - level)/level; roof: (level - min close)/level). NULL unless outcome = held.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.break_depth_pct IS 'Broken-branch amplitude: max adverse close within 20 trading days of the break vs level, as a positive fraction (floor: (level - min close)/level; roof: (max close - level)/level). NULL unless outcome = broken.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.days_to_break IS 'Trading days between retest_date and the first beyond-level close. NULL unless outcome = broken.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.clean_break IS 'TRUE when the close 20 trading days after the break is still beyond the level (the old floor now behaves as a roof / vice versa — role flip). NULL unless outcome = broken.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.later_break IS 'TRUE when an outcome=held event''s level was eventually exceeded by a close on a LATER retest (before the data end). NULL unless outcome = held.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_rebounds.retest_episodes IS 'Count of distinct retest episodes (zone-touch runs separated by >= 10 out-of-zone trading days) before the break — or before the data end when the level never broke. NULL for no_rebound / never_retested.';
