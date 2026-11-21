/**
 * families/volatility — the volatility family (signal_type
 * "volatility"): the realized-volatility panel (analysis.volatility,
 * the daily per-security vol build) as rule cells — the
 * LEVERAGE-EFFECT tails (realized-vol peaks cluster at price troughs:
 * "local top volatility signals local low price", the research
 * premise of the 2026-11-18 build). Two axes x the nested tail grid:
 *   hv20_*  the canonical 20-row close-to-close realized vol
 *   ewma_*  the RiskMetrics fast vol (λ=0.94)
 * each sub type carrying both actions — spike BUY (the vol axis above
 * its own trailing top-q adaptive tail with price below its
 * 5-rows-ago close, the panic washout) and crush SELL (below its
 * bottom tail with price within 2% of its trailing-60-row high —
 * complacency at the top, exit timing). The sub types carry no record
 * word of their own (the raw literal — "hv20_1pct" — is the record's
 * word); the rule bar decoder (ruleParams.ts "rolling_quantile", the
 * mov_rsi path) renders the tail bar.
 */
import type { SignalFamily } from "./index";

export const volatilityFamily: SignalFamily = {
  label: "Volatility",
};
