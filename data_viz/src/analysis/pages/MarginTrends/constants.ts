/**
 * Shared constants for the Margin Trends analysis page sub-modules.
 */

/** Correlation window labels shown in the ToggleButtonGroup. */
export const CORR_WINDOWS = [5, 20, 60, 120, 255] as const;
export type CorrWindow = (typeof CORR_WINDOWS)[number];

/** Attribution toggle: Index (weighted-avg constituent-stock margin via
 *  the margin_index_series VIEW) or ETF (the ETF's own margin). */
export type MarginAttribution = "index" | "etf";
export const ATTRIBUTION_OPTIONS: MarginAttribution[] = ["index", "etf"];

/** Series toggle: balance (融资余额, STOCK) or buy (融资买入额, FLOW). */
export type MarginSeries = "balance" | "buy";
export const SERIES_OPTIONS: MarginSeries[] = ["balance", "buy"];
