-- ============================================================================
--  Oracle Signals — the HINDSIGHT oracle tier: per-(sec_type, code, date)
--  perfect-foresight actions at the LOCAL EXTREMES for the 10 study
--  indices.
--
--    Table: analysis.oracle_signals
--    PK: (sec_type, code, date)
--
--  DECOUPLED from the rule tier (analysis_signals.* — no rule cells, no
--  replay, no live wiring): this table is the ground-truth label layer
--  that looks at FUTURE bars and records what the optimal action would
--  have been. Built by analysis.oracle_signals; serves the Signals →
--  Oracle page (/analysis/signals/oracle) through /api/analysis/oracle/*.
--
--  DECISION GRAMMAR v7 (2026-11-20; constants mirrored in
--  analysis/oracle_signals/config.py — the single source). The v1 gate
--  apparatus (5d/20d hindsight windows, fluctuation gates, long-horizon
--  extremum gates, post-action quiet, the 252d forward-context trail)
--  and v2's +-5 pivot windows are RETIRED. v3 set the 1-day stride
--  ("for 5d window, update that stride should be 1d but keep the 5d
--  one action window"); v3.1 split the one-action window PER SIDE
--  ("within 5d action window can do both buy and sell"); v4 added the
--  FORWARD-TREND GRADING (user-directed "should look future 60d if
--  there are big rise/drop trend, should tune down action confidence
--  even take no action" — the 2020-01 small-gap example; calibrated by
--  temp_scripts/oracle_forward60_study.py,
--  docs/oracle_forward60_confidence_study_2026-11-19.md); v5 added the
--  BACKWARD LEG (user-directed "should consider before and after
--  local min/max rises and falls" — the 2020-11-09 / 2024-02-23 /
--  2025-05-14 top examples; calibrated by temp_scripts/
--  oracle_backward_leg_study.py, docs/oracle_backward_leg_study_
--  2026-11-19.md); v6 added the PREPARE-TO-BUY-DEEP RULE (user-
--  directed "2024-08 has small peaks that could be noisy, while
--  2024-09 has a big drop, so should not trigger buy action but sell
--  some in advance to prepare more cash to buy in deep in next
--  month"; calibrated by temp_scripts/oracle_prepare_deep_study.py,
--  docs/oracle_prepare_deep_study_2026-11-20.md); v7 widened it into
--  the GENUINE-EXTREME RULE — both sides x both directions (user-
--  directed "the buy in deep rule should also account for recent
--  rises unless the rises are small, refactor the buy in deep sell
--  in high logic to take care of recent and future min/max";
--  calibrated by temp_scripts/oracle_genuine_extreme_study.py,
--  docs/oracle_genuine_extreme_study_2026-11-20.md); v7.1 (the same
--  day, user-directed "it is still ok to buy if it is a local deep
--  min despite even in 20d there is a deeper local min ... the buy
--  deep logic is more about filter small fluctuation noises;
--  2024-10-16 should be a buy day") REFINED the BUY side to the
--  GENUINE-DEEP-MIN TEST — the drawdown into the pivot decides: a
--  genuine deep min always buys, only shallow wiggles before an
--  imminent deeper level omit.
--    • LOCAL EXTREME, 1-DAY STRIDE over the 5-BAR WINDOW: every bar i
--      is tested as a local extreme over [i-2, i+2] (2 rows each
--      side; STRICT vs the left neighbors so a plateau's FIRST bar
--      wins, ties allowed on the right), LOCAL MAX mirrored. The
--      motivating case for shrinking v2's +-5 (11-bar) window: a peak
--      failed whenever ANY higher close sat within 5 trading rows on
--      either side — 931743's 2025-09-24 spike top (5126) was left
--      unmarked solely because the 10-09 higher top (5350) fell
--      exactly 5 rows later (Golden Week compresses the calendar),
--      so September showed a lone buy and a huge unmarked peak.
--    • THE 5D ONE-ACTION WINDOW, PER SIDE (v3.1): consecutive actions
--      of the SAME side sit >= 5 traded rows apart — any
--      5-consecutive-row window holds at most ONE buy AND at most
--      ONE sell (a buy and a sell may sit within the same 5 rows; the
--      v3 either-side window suppressed real opposite extremes —
--      000001's 2025-08-01 local min inside the 07-30 sell unit, its
--      2020-07-22 local max inside the 07-16 buy unit, its 2022-03-01
--      local max inside the 02-24 buy unit). Selection walks the
--      candidates chronologically with one cursor per side: the FIRST
--      candidate of a side's unit claims it; a MORE EXTREME same-side
--      candidate within the unit's 5 rows UPGRADES the placement to
--      that bar (the unit window re-extends from it); every other
--      candidate inside the unit is suppressed. A bar is never both
--      sides (strict local min and local max are mutually
--      exclusive), so the PK cannot collide.
--    • THE FAVORABLE-MOVE GRADING (v4 forward + v5 backward): each
--      PLACED pivot is validated by its favorable excursion on EITHER
--      side — a BUY by the biggest RISE within the next
--      LOOKFORWARD_DAYS (20 — user-directed 2026-11-20 "let future
--      look only be 20d not 60d"; the v4 2026-11-19 calibration ran
--      60) traded rows OR the biggest DROP within
--      the LOOKBACK_DAYS (20 — 2026-11-21 user-directed "also only
--      look back 20d not 60d"; the v5 calibration ran 60) rows before
--      it; a SELL by the biggest DROP after / biggest RISE before
--      (the max favorable excursion on both sides). Each leg grades
--      0..100 on its own linear ramp
--      from CONF_NO_ACTION_MOVE (2%) to CONF_FULL_MOVE (10%,
--      clipped — fixed % bars, user choice; the measured buy/sell
--      distributions are near-identical, so the bars are SYMMETRIC);
--      the pivot's CONFIDENCE is the WEIGHTED SUM — the pre-action
--      leg at CONF_BACK_WEIGHT (30%), the forward leg at
--      CONF_FWD_WEIGHT (70%; user-directed "let rises/drops before
--      action date with confidence weighted 30%, while future
--      rises/drops weighted 70%") — and the pivot emits when its
--      weighted grade is positive: EITHER leg above the floor acts,
--      BOTH legs below the floor OMITS the row — the oracle takes NO
--      ACTION at that pivot (the 2020-01 small-gap chop: the
--      +0.7%/+1.6% buys die — their backward legs are dust too; the
--      v5 rescues — 000001's 2020-11-09 top (+4.9% in / +1.9% out),
--      its 2024-02-27 top (+11.6% in / +1.9% out), its 2025-05-14
--      top (+9.9% in / +1.9% out) — grade on their run-up share,
--      conf 30 = a full backward grade alone). The BACKWARD leg is
--      fully realized the moment the pivot is confirmed (it reads
--      only past rows), so a frontier pivot with a big
--      run-up/drawdown grades at its 30% share now (conf only grows
--      as the forward realizes); only a backward-dust UNREALIZED
--      window (< 60 future rows at the data frontier) is NOT graded:
--      the row emits with confidence NULL — unknown is not small —
--      and is re-graded (or dropped) by the DAILY incremental's diff
--      (user-directed 2026-11-21 "the incremental daily run should
--      be able to update last 20d signals to add/remove signals
--      since local max/min are known" — the former weekly tier +
--      config.WEEKLY_GATE_DAYS gate retired; the future horizons
--      are ALL 20, user-directed "only 20d future look forward").
--      Omitted pivots still OCCUPY their 5-row unit (placement
--      precedes grading).
--    • THE GENUINE-EXTREME RULE (v7, both sides x both directions —
--      each side speaks only at a genuine extreme of its own
--      horizon; the horizons UNIFIED at 20, user-directed "why 30?
--      already said only 20d future look forward" — every future-
--      reading window in the grammar is the same 20 traded rows;
--      the BUY side REFINED v7.1, user-directed "it is still ok to
--      buy if it is a local deep min despite even in 20d there is a
--      deeper local min ... the buy deep logic is more about filter
--      small fluctuation noises; 2024-10-16 should be a buy day"):
--      a BUY passes the GENUINE-DEEP-MIN TEST — the drawdown INTO
--      the pivot from the prior PREPARE_HORIZON_DAYS (20) rows'
--      high at PREPARE_DEEP_MARGIN (5%)+ makes it a GENUINE DEEP
--      MIN that ALWAYS buys, even when a deeper local min follows
--      within the 20d horizon (that deeper min gets its own buy
--      when it forms; the v7-era kills this retires: 000001's
--      2024-10-17 pullback low -9.2% off the 10-08 top was killed
--      for sitting +17.2% above the 09-13 floor — the wrong lens;
--      the user's 2024-10-16 example is this pivot on the codes
--      where it lands 10-16); a shallower min is a SMALL
--      FLUCTUATION — the noise filter: it omits only when a
--      materially deeper level (5%+ below) is imminent within the
--      next 20 rows (000001's 2024-08-22 chop wiggle @2848 saw the
--      09-13 deep -5.1% below 16 rows out), and keeps otherwise
--      (08-05/08-14, whose within-20d deepest is dust, stay).
--      A SELL must be a genuine HIGH —
--      the RECENT leg omits a sell sitting SELL_HIGH_MARGIN (5%) or
--      more below the prior SELL_HIGH_HORIZON_DAYS (20) rows'
--      ceiling (a weak bounce in a decline, not a high; measured
--      kills at 20x5%: 423 of 1,684 v6-kept sells), and the FUTURE
--      leg omits a sell whose next 20 rows contain a close 5%+ above
--      (a materially higher exit is imminent — the waypoint sells
--      the 924-style melt-up runs through; 401 measured kills; the
--      2024-08 advance sells stay live — their 20-row margins:
--      fall-from-ceiling 2.4..3.8%, coming-higher <= +1.4%). The
--      future legs read the
--      REALIZED portion of the window and are MONOTONE at the
--      frontier (a partial window already showing the disqualifying
--      level omits now; the daily incremental re-decides the rest as
--      the window realizes); the recent
--      legs are
--      realized at placement (past rows only). A suppressed pivot
--      still OCCUPIES its 5-row unit and still anchors later rows'
--      ext_* legs (placement precedes grading).
--    • A candidate needs its 2 right-side bars, so the trail ends 2
--      bars before the data frontier; closed units are immutable, but
--      the frontier unit's placement can UPGRADE forward as bars
--      arrive and frontier pivots re-grade — the DAILY incremental
--      run diffs the target against the engine's full re-decision
--      (upserts every missing/changed decision, deletes every row no
--      longer decided — the previous frontier action's NULL
--      swing-target legs close at any depth of history the moment
--      the opposite pivot is placed).
--    • The ext_* legs carry the row's hindsight SWING TARGET: the
--      NEXT PLACED ACTION of the OPPOSITE side (a BUY's target = the
--      next placed sell; a SELL's = the next placed buy) — PLACED-
--      level: the target may be a pivot whose own row was omitted by
--      the grading floor (the swing is a fact about the price path,
--      not about the tradable action); NULL when that opposite
--      action is not placed yet (the frontier's last unit).
--
--  ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each study index's hindsight oracle action at its validated local extremes — 5-bar-window pivots scanned daily, one buy at a local minimum and one sell at a local maximum per 5-day window (the one-action window holds per side), each side speaking only at a GENUINE extreme of its own horizon (a buy passing the genuine-deep-min test: the drawdown into the pivot from the prior 20 trading days' high is 5%+ — a genuine deep min always buys even when a deeper local min follows within 20 days, while a shallower min is a small fluctuation that omits only when a materially deeper level is imminent within those 20 days; a sell only at a genuine high: no 5%+ fall from the prior 20-day ceiling and no close 5%+ above within the next 20 days), each surviving pivot graded 0-100 by its biggest favorable rise/drop on either side of the pivot (within the next or the previous 60 trading days) and omitted when both legs miss the 2% no-action floor, with the next opposite placed pivot as the hindsight swing target.
-- tags: oracle, 先知, hindsight, 后视, perfect foresight, 事后最优, local minimum, 局部最低, local maximum, 局部最高, pivot, 拐点, buy the dip, 低吸, sell the rally, 高抛, confidence, 置信度, forward trend, 前瞻趋势, backward leg, 前段行情, run-up, 冲高, drawdown, 回落, swing target, 波段目标, prepare to buy deep, 备资深买, coming deeper low, 更深低点, falling knife, 不接飞刀, genuine extreme, 真极值, pullback not deep, 回调非底, weak bounce, 弱反弹, genuine high, 真高点


CREATE TABLE IF NOT EXISTS analysis.oracle_signals (
    sec_type    SMALLINT      NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code        TEXT          NOT NULL,
    date        DATE          NOT NULL,  -- the PIVOT date (the local extreme itself — the action's day)

    action      SMALLINT      NOT NULL,  -- label code (static.label_dim 'oracle_action'): 0=buy (local min) 1=sell (local max)
    window_days SMALLINT      NOT NULL,  -- the grammar's 5-row window: the local-extreme test's total span (2 rows each side) AND the one-action span PER SIDE (one buy + one sell per 5 rows); 5

    ext_date    DATE,                    -- the NEXT OPPOSITE placed pivot — the row's hindsight swing target; NULL at the frontier
    ext_offset  SMALLINT,                -- traded rows pivot -> opposite pivot (>= 1); NULL at the frontier
    ext_price   DOUBLE PRECISION,        -- the opposite pivot's close (the hindsight exit / re-entry price); NULL at the frontier
    confidence  DOUBLE PRECISION,        -- the v5 favorable-move grade 0..100: the WEIGHTED SUM of the two legs' linear-ramp grades — CONF_BACK_WEIGHT (0.3) x the backward-20-row move INTO the pivot + CONF_FWD_WEIGHT (0.7) x the forward-20-row move after it (all windows 20 both directions, user-directed 2026-11-20 "let future look only be 20d not 60d" + 2026-11-21 "also only look back 20d not 60d"; each leg ramped 2% floor / 10% full); a pivot failing any leg of the v7 genuine-extreme gate never reaches the grading; NULL only while the forward window is unrealized AND the backward leg misses the floor (frontier pivots)

    created_at  TIMESTAMPTZ    NOT NULL DEFAULT now(),

    CONSTRAINT pk_oracle_signals PRIMARY KEY (sec_type, code, date),
    CONSTRAINT chk_oracle_signals_action
        CHECK (action IN (0, 1, 2)),  -- label codes: 0=buy 1=sell (2 no_action — v1 decode-only, never emitted)
    CONSTRAINT chk_oracle_signals_window
        CHECK (window_days > 0),
    CONSTRAINT chk_oracle_signals_confidence
        CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 100)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'oracle_signals', 16);

CREATE INDEX IF NOT EXISTS idx_oracle_signals_date
    ON analysis.oracle_signals (date);
CREATE INDEX IF NOT EXISTS idx_oracle_signals_ext_date
    ON analysis.oracle_signals (ext_date);

COMMENT ON TABLE  analysis.oracle_signals IS 'Hindsight oracle actions per (sec_type, code, date) for the 10 study indices — the perfect-foresight tier, DECOUPLED from the analysis_signals rule tier: v7 pivot grammar (2026-11-20) — every local min of the 5-bar window (2 rows each side) takes a buy, every local max a sell, consecutive same-side actions >= 5 traded rows apart (a 5-row window holds one buy AND one sell), then the GENUINE-EXTREME gate on both sides (a BUY passing the genuine-deep-min test: the drawdown into the pivot from the prior 20 rows'' high is 5%+ — a genuine deep min always buys even when a deeper local min follows within 20 days, a shallower min being a small fluctuation that omits only when a materially deeper level is imminent within those 20 rows; a SELL only at a genuine high: no 5%+ fall from the prior 20 rows'' ceiling — a weak bounce is not a high — and no close 5%+ above within the next 20 rows — a higher exit is imminent), each surviving pivot then GRADED 0-100 by its favorable move on EITHER side (buy: biggest rise after / biggest drop before, sell: biggest drop after / biggest rise before — the forward window LOOKFORWARD_DAYS (20) traded rows, user-directed 2026-11-20 "let future look only be 20d not 60d"; the backward LOOKBACK_DAYS unified at 20, 2026-11-21 user-directed "also only look back 20d not 60d"; each leg ramped linearly 2% no-action floor / 10% full and combined as the WEIGHTED SUM 0.3 * pre-action leg + 0.7 * forward leg), with a zero weighted grade (both legs below the floor) the pivot emits NOTHING (a backward-dust unrealized frontier window rides confidence NULL), each kept row carrying the next opposite placed pivot as its hindsight swing target. A pivot needs only its 2 right-side confirmation bars, so the trail ends 2 rows before the data frontier. Built by analysis.oracle_signals; serves the Signals Oracle page via /api/analysis/oracle/*.';
COMMENT ON COLUMN analysis.oracle_signals.sec_type    IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf, 1 = index, 2 = stock. Run scope: 1 (index) only for now — the 10 study indices.';
COMMENT ON COLUMN analysis.oracle_signals.code        IS 'Security code (bare index code e.g. 000001).';
COMMENT ON COLUMN analysis.oracle_signals.date        IS 'The action date — the local extreme bar itself (a candidate of the 5-bar window [date-2, date+2] that won its side''s 5d one-action selection). The oracle''s action executes AT the extreme.';
COMMENT ON COLUMN analysis.oracle_signals.action      IS 'The hindsight action as its static.label_dim (''oracle_action'' domain) SMALLINT code: 0 = buy (a LOCAL MIN of the 5-bar window), 1 = sell (a LOCAL MAX). v7 emits only 0/1 (a pivot failing any leg of the genuine-extreme gate, or one whose both favorable legs miss the 2% no-action floor, is OMITTED, not stood down); 2 no_action is the v1 era''s explicit stand-down row, decode-only.';
COMMENT ON COLUMN analysis.oracle_signals.window_days IS 'The grammar''s 5-row window, serving both roles: the local-extreme test (close[date] is the extreme of the 2 rows on each side — WINDOW_SIDES) and the ONE-ACTION span PER SIDE (any 5 consecutive rows hold at most one buy AND at most one sell — a buy and a sell may sit within the same 5 rows; ONE_ACTION_SPAN). Constant 5 (config.py).';
COMMENT ON COLUMN analysis.oracle_signals.ext_date    IS 'The row''s hindsight SWING TARGET: the NEXT PLACED action of the OPPOSITE side (a buy''s target = the next sell; a sell''s = the next buy) — placed-level: the target may be a pivot whose own row was omitted by the grading floor. NULL when that opposite action is not placed yet (the frontier''s last unit).';
COMMENT ON COLUMN analysis.oracle_signals.ext_offset  IS 'Traded-row distance pivot -> its opposite-pivot swing target (>= 1). NULL at the frontier.';
COMMENT ON COLUMN analysis.oracle_signals.ext_price   IS 'The swing target''s close — the hindsight exit price for a buy / re-entry for a sell. NULL at the frontier.';
COMMENT ON COLUMN analysis.oracle_signals.confidence IS 'The v5 FAVORABLE-MOVE GRADE, 0..100 (user-directed "look future 60d if there are big rise/drop trend, tune down action confidence even take no action" + "consider before and after local min/max rises and falls" + "rises/drops before action date weighted 30%, future weighted 70%"): the WEIGHTED SUM of the two favorable legs'' linear-ramp grades — each leg graded 0..100 from CONF_NO_ACTION_MOVE (2%) to CONF_FULL_MOVE (10%), clipped — weighted CONF_BACK_WEIGHT (0.3) for the BACKWARD leg (a buy''s biggest drop / a sell''s biggest rise within the LOOKBACK_DAYS rows before the pivot — the run-up into a top / the drawdown into a bottom, fully realized the moment the pivot is confirmed; UNIFIED at 20 rows, 2026-11-21 user-directed "also only look back 20d not 60d" — the v5 2026-11-19 calibration ran 60) and CONF_FWD_WEIGHT (0.7) for the FORWARD leg (a buy''s biggest rise / a sell''s biggest drop within the next LOOKFORWARD_DAYS traded rows — 20 since the 2026-11-20 user directive "let future look only be 20d not 60d"; the v4 2026-11-19 calibration ran 60). The v7 GENUINE-EXTREME gate runs FIRST on both sides (buy, refined v7.1 "it is still ok to buy if it is a local deep min despite even in 20d there is a deeper local min": the drawdown INTO the pivot from the prior PREPARE_HORIZON_DAYS=20 rows'' high at PREPARE_DEEP_MARGIN=5%+ makes it a genuine deep min that always buys, a shallower min being a small fluctuation that omits only when a 5%+-deeper level is imminent within those 20 rows; sell: no 5%+ fall from the prior SELL_HIGH_HORIZON_DAYS=20 rows'' ceiling and no close 5%+ above within the next 20 rows — all horizons unified at 20, user-directed "only 20d future look forward") — a pivot failing its gate is never graded. A zero weighted grade (both legs below the floor) OMITS the row entirely; an UNREALIZED frontier window whose backward leg also misses the floor emits NULL — unknown is not small — re-graded by the daily incremental''s diff (the engine re-decides the full grid every run; a frontier row with a realized backward grade emits its 30% share and only grows). Calibrated by temp_scripts/oracle_forward60_study.py + temp_scripts/oracle_backward_leg_study.py + temp_scripts/oracle_prepare_deep_study.py + temp_scripts/oracle_genuine_extreme_study.py (docs/oracle_forward60_confidence_study_2026-11-19.md, docs/oracle_backward_leg_study_2026-11-19.md, docs/oracle_prepare_deep_study_2026-11-20.md, docs/oracle_genuine_extreme_study_2026-11-20.md).';
