/**
 * families/rsi — the mov_rsi family (signal_type "mov_rsi"): the
 * display label; the RSI windows carry no record word of their own
 * (the raw sub_type literal — "rsi14_1pct" — is the record's word).
 */
import type { SignalFamily } from "./index";

export const rsiFamily: SignalFamily = {
  label: "RSI",
};
