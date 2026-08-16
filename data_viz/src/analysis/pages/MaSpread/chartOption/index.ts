/**
 * ECharts option builders for MA-Spread charts.
 *
 * This is the main entry point for chart option building.
 * Import from "./chartOption" in MaSpreadPanel.tsx.
 */

export { buildPairOption, type BuildPairOptionArgs, type TradingAmtMode } from "./buildPairOption";
export { buildAmtEnvelopeOption, type BuildAmtEnvelopeOptionArgs } from "./buildAmtEnvelopeOption";
export { shortLabel, computeTrendBands, type TrendBand, type TrendType } from "./trendBands";
