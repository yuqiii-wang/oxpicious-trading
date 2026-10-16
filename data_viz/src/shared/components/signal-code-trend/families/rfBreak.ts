/**
 * families/rfBreak — the rf_break family (signal_type "rf_break"):
 * the roof/floor Donchian breakout (the prior day's structural
 * roof/floor level from the roof/floor estimates store; roof = the
 * breakout BUY, floor = the breakdown exit-timing SELL, donchian +
 * median consensus methods over the {60, 120, 255, 500}d windows).
 * The sub types carry no record word of their own (the raw literal —
 * "rf255_med_roof" — is the record's word).
 */
import type { SignalFamily } from "./index";

export const rfBreakFamily: SignalFamily = {
  label: "Roof/Floor",
};
