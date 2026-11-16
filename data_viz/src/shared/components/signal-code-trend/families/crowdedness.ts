/**
 * families/crowdedness — the crowdedness family (signal_type
 * "crowdedness"): the nightly market crowdedness composite (the
 * pool-level crowding gauge over each index's member-stock pool) as
 * rule cells ON THE INDEX ITSELF, triggered by the crowd's
 * RECENT-EXTREME state gated by the underlying's own direction (v3,
 * 2026-11-16 — the v2 long-percentile tails measured out). TWO
 * HOT-SET BASES x the score/ma5 axes x the two states: crowd_roll_*
 * the DAILY-ROLLING hot set (analysis.market_crowdedness),
 * crowd_year_* the THIS-YEAR pinned hot set (the date's own calendar
 * year's frozen roster — analysis.market_crowdedness_year); within
 * each base _score_* the raw composite, _ma5_* the ONLINE read.
 *   _drain   BUY   the crowd at its 60d low while the index rose
 *   _pilein  SELL  the crowd at its 60d high while the index fell
 * plus the v4 turning-date catch (2026-11-16): _drain_floor BUY the
 * crowd at its trailing-year bottom while the index ticks up,
 * crowd_tech_washout BUY the hot set under water (the capitulation
 * catch), crowd_tech_euphoria(_div) SELL the blow-off stretch (+ the
 * price/crowd divergence). Bar words decode via ruleParams.ts
 * ("recent_extreme" / "crowd_floor" / "hot_tech_washout" /
 * "hot_tech_euphoria").
 */
import type { SignalFamily } from "./index";

export const crowdednessFamily: SignalFamily = {
  label: "Crowdedness",
};
