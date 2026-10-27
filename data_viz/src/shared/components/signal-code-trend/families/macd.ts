/**
 * families/macd — the macd family (signal_type "macd"): the classic
 * MACD signal-line crossover family (2026-10-26, the typical-EMA
 * study's product) over the classic 12/26/9 spans — macd_cross_up =
 * the bullish crossover BUY (the momentum turn), macd_cross_dn = the
 * bearish crossover SELL (exit timing). The sub types carry no record
 * word of their own (the raw literal — "macd_cross_up" — is the
 * record's word); the rule bar decoder (ruleParams.ts "macd_cross")
 * renders the spans.
 */
import type { SignalFamily } from "./index";

export const macdFamily: SignalFamily = {
  label: "MACD",
};
