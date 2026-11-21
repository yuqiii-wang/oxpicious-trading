/**
 * types — shared type definitions for the strategy tier's _common
 * components (no runtime code).
 */
import type { StrategyPeriodType } from "@shared/types";

/**
 * Selected risk-analytics period — set when the user clicks a bar in the
 * RiskPanel chart. (Moved here from the retired hand-rolled backtest
 * option builder: the singleton chart is the inherited code trend now,
 * which carries no gain/loss span overlay — the RiskPanel highlight is
 * the period selector.)
 */
export interface SelectedPeriod {
  periodType: StrategyPeriodType;
  periodValue: string;
  /** true → gain (green shade), false → loss (red shade). */
  isGain: boolean;
}
