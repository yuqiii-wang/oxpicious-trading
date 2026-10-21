/**
 * Shared types for the MA-Spread analysis page sub-modules.
 */
import type {
  MaSpreadSecType,
  MovAveSpreadHighLowStreak,
  MovAveSpreadMomentumDay,
  MovAveSpreadOhlcGapDay,
  MovAveSpreadOhlcRow,
  MovAveSpreadPairSeries,
  MovAveSpreadPriceVsAmtDay,
  MovAveSpreadRoofFloorEstimateRow,
  MovAveSpreadRoofFloorRebound,
  MovAveSpreadRsiRankDay,
} from "@shared/types";

/** Props for the MaSpreadPanel component. */
export interface PanelProps {
  code: string;
  name: string;
  secType: MaSpreadSecType;
}

/**
 * The pair chart's right-axis overlay, switched by the Amt | Slopes |
 * Curvatures group in the card header: "amt" shows the lowkey
 * trading-amount bars; slope/curv swap them for the DB-stored direct
 * derivative lines of the price and the 5/20-day MA (or 6/20-day EMA
 * while an EMA pair chip is selected). The RSI Trends overlay rides the
 * "amt" slot via its own button row beneath the chart; Slopes/Curvatures
 * keep precedence over it, and a Roof/Floor window's change-since-anchor
 * plot owns the slot while enabled.
 */
export type ChartOverlay = "amt" | "slope" | "curv";

/** On-demand metric groups loaded so far (per code + refresh). */
export interface ExtrasState {
  amtPairs?: MovAveSpreadPairSeries[];
  ohlc?: MovAveSpreadOhlcRow[];
  rfEstimates?: MovAveSpreadRoofFloorEstimateRow[];
  rfRebounds?: MovAveSpreadRoofFloorRebound[];
  highLowStreaks?: MovAveSpreadHighLowStreak[];
  priceVsAmt?: MovAveSpreadPriceVsAmtDay[];
  ohlcGaps?: MovAveSpreadOhlcGapDay[];
  rsiRanks?: MovAveSpreadRsiRankDay[];
  momentum?: MovAveSpreadMomentumDay[];
}

/** The panel's single shade slot's families — activating one clears every
 *  other family's picks (trend phases, RSI Top-% pct, High/Low Streaks,
 *  Px-Vol States, Top-10% OHLC Days, Market Regimes). */
export type ShadeGroupKey = "trend" | "rsiPct" | "streaks" | "pxvol" | "gaps" | "regimes";

/** The Top-10% OHLC Days row's independent toggles. */
export interface GapMarksState {
  spike: boolean;
  flush: boolean;
}

// Re-export the API types for convenience so callers can import everything
// from a single entry point if desired.
export type { MaSpreadSecType } from "@shared/types";
