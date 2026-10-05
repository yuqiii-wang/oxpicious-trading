/**
 * ECharts option builders for MA-Spread charts.
 *
 * This is the main entry point for chart option building.
 * Import from "./chartOption" in MaSpreadPanel.tsx.
 *
 * Layout: buildPairOption / buildAmtEnvelopeOption / tooltipFormatter are
 * slim orchestrators; each header/row button group owns its computation
 * and series in its own subdirectory — bollinger/ (the Bollinger
 * dropdown), rsiTrend/ + derivTrend/ + tradingAmt/ (the Amt | RSI Trends
 * | Slopes | Curvatures header switch), roofFloor/ (the Roof/Floor
 * Trendlines row + "shade" toggle), streaks/ (High/Low Streaks),
 * pxVol/ (Px-Vol States), ohlcGaps/ (Top-10% OHLC Days) — and the
 * plumbing the groups share lives in _common/. Market Regimes has no
 * chartOption-side code (its spans are assembled in the panel via the
 * shared regimeBands kit and render through _common's overlay series).
 */

// ---- Orchestrators + the pair chart's core option builder ---------------
export { buildPairOption, type BuildPairOptionArgs } from "./buildPairOption";
export { buildAmtEnvelopeOption, type BuildAmtEnvelopeOptionArgs } from "./buildAmtEnvelopeOption";

// ---- Shared plumbing (types the panel consumes) --------------------------
export type { TradingAmtMode, DerivTrendSpec, ShadeOverlay } from "./_common";

// ---- Trend classification (tooltip-only, no button group) ----------------
export { shortLabel, computeTrendBands, type TrendBand, type TrendType } from "./trendBands";

// ---- Px-Vol States group --------------------------------------------------
export {
  computePxVolStates,
  pxVolMatchRuns,
  pxVolRunsToMarkArea,
  pxVolShadeColor,
  pxVolAccentColor,
  pxVolReading,
  PX_VOL_SPEED_OPTIONS,
  PX_VOL_VOL_OPTIONS,
  type PxVolSpeed,
  type PxVolVolState,
  type PxVolDayState,
  type PxVolRun,
} from "./pxVol";
